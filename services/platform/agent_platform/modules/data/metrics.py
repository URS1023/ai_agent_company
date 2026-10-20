"""执行已保存且可见的指标口径，复用固定聚合；口径和原始查询同时保留授权证据。"""
from copy import deepcopy

from pydantic import ValidationError

from agent_platform.core.errors import DomainError
from agent_platform.core.records import RecordStore

from . import models as m
from .processing import run_pipeline
from .schemas import MetricDefinition
from .values import ARITHMETIC, calculate, decimal_text, decimal_value


def validate_definition(definition):
    """旧目录可保留草稿；进入执行前必须符合版本1，不能把说明文字当公式执行。"""
    try:
        parsed = MetricDefinition.model_validate(definition).model_dump()
        if parsed["kind"] == "aggregate":
            if parsed["aggregation"] is None or parsed["numerator"] is not None or parsed["denominator"] is not None:
                raise ValueError("请选择一项聚合，单指标不接受分子分母")
        elif parsed["numerator"] is None or parsed["denominator"] is None or parsed["aggregation"] is not None:
            raise ValueError("比率指标须提供分子和分母聚合")
        if parsed["kind"] == "ratio" and any(item["data_type"] == "text" and item["function"] not in ("count", "count_distinct") for item in (parsed["numerator"], parsed["denominator"])):
            raise ValueError("比率的分子分母须为数值聚合或计数")
        if parsed["kind"] == "aggregate" and parsed["aggregation"]["data_type"] == "text" and parsed["aggregation"]["function"] in ("min", "max") and decimal_value(parsed["scale"]) != 1:
            raise ValueError("文字指标不能乘比例系数")
        decimal_value(parsed["scale"])
        if set(parsed["group_by"]).intersection({"value", "metric_unit", "status", "numerator", "denominator"}):
            raise ValueError("分组字段与结果字段重名，请先在查询中设置别名")
        return parsed
    except (ValidationError, ValueError, ArithmeticError) as exc:
        raise DomainError(422, "metric_definition_invalid", "指标口径尚未完整配置，请检查聚合和分子分母", {"path": "definition"}) from exc


def metric_result(rows, columns, definition):
    specs = [definition["aggregation"]] if definition["kind"] == "aggregate" else [definition["numerator"], definition["denominator"]]
    names = ["value"] if definition["kind"] == "aggregate" else ["numerator", "denominator"]
    aggregates = [{**spec, "name": name} for name, spec in zip(names, specs)]
    computed = run_pipeline(rows, columns, [{"type": "aggregate", "group_by": definition["group_by"], "aggregations": aggregates}])
    unit_column = "metric_unit" if "unit" in definition["group_by"] else "unit"
    for row in computed["rows"]:
        row[unit_column] = definition["unit"]
        if definition["kind"] == "ratio":
            denominator, numerator = row["denominator"], row["numerator"]
            row["value"] = None
            row["status"] = "missing_denominator" if denominator is None else "zero_denominator" if decimal_value(denominator) == 0 else "no_data" if numerator is None else "computed"
            if row["status"] == "computed":
                value = calculate("divide", [decimal_value(numerator), decimal_value(denominator)])
                row["value"] = decimal_text(calculate("multiply", [value, decimal_value(definition["scale"])]))
        else:
            row["status"] = "no_data" if row["value"] is None else "computed"
            if row["value"] is not None and decimal_value(definition["scale"]) != 1:
                row["value"] = decimal_text(calculate("multiply", [decimal_value(row["value"]), decimal_value(definition["scale"])]))
        if not rows:
            row["status"] = "no_data"
    computed["columns"] = definition["group_by"] + (["value", unit_column, "status"] if definition["kind"] == "aggregate" else ["value", "numerator", "denominator", unit_column, "status"])
    computed["metric_unit_field"] = unit_column
    computed["arithmetic"] = ARITHMETIC
    return computed


class MetricService:
    def __init__(self, session, actor):
        self.session, self.actor = session, actor

    def execute(self, metric_id, data):
        """只消费当前获授权的完整查询快照，执行不会连接数据库或调用模型。"""
        from .service import DataService, view
        self.actor.require("m05.read")
        self.actor.require("m06.write")
        metric = RecordStore(self.session, self.actor).get("business_metric", metric_id)
        if data.get("revision") is not None and data["revision"] != metric["revision"]:
            raise DomainError(409, "revision_conflict", "指标口径已更新，请刷新后执行")
        definition = validate_definition(metric.get("definition", {}))
        query = DataService(self.session, self.actor).get_query(data["query_id"])
        if metric.get("datasource_id") and metric["datasource_id"] != query.datasource_id:
            raise DomainError(422, "metric_source_mismatch", "查询与指标登记的数据源不一致")
        if query.result["truncated"]:
            raise DomainError(422, "metric_input_truncated", "查询已截断，请缩小范围后再计算指标")
        try:
            result = metric_result(query.result["rows"], query.result["columns"], definition)
        except (ValueError, ArithmeticError, TypeError) as exc:
            raise DomainError(422, "metric_calculation_invalid", "指标计算超出数值范围，请检查口径及输入", {"path": "definition"}) from exc
        operation = {"type": "metric", "metric_id": metric_id, "metric_revision": metric["revision"], "definition_snapshot": deepcopy(definition)}
        result["metric"] = {"id": metric_id, "name": metric["name"], "revision": metric["revision"], "definition": deepcopy(definition)}
        record = m.ProcessingJob(workspace_id=self.actor.workspace_id, name=metric["name"], query_id=query.id, operations=[operation], result=result, status="succeeded")
        self.session.add(record)
        self.session.flush()
        RecordStore(self.session, self.actor).audit("metric.execute", "processing_job", record.id, {"metric_id": metric_id, "metric_revision": metric["revision"], "query_id": query.id})
        return view(record)
