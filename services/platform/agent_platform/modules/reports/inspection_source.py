"""检验运行的可信报告投影；冻结事实，读取时重查当前源权限。"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import select

from agent_platform.core.errors import DomainError
from agent_platform.core.models import Task, TaskEvent, User

from ..data.service import DataService
from ..data.sql_safety import SqlFieldValidationError, ValidationError
from ..manufacturing import models as manufacturing


VIEWS = frozenset({"meta", "summary", "outcomes", "metrics", "reviews", "standard"})
OUTCOMES = {"passed": "合格", "failed": "不合格", "review": "待复核", "incomplete": "数据不完整"}


def digest(value):
    """只对规范JSON计算摘要，避免键顺序影响证据一致性。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def timestamp(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()
    return value


def is_inspection_binding(binding):
    return isinstance(binding, dict) and binding.get("source_type") == "inspection_run"


def validate_binding(binding):
    """查询和检验绑定严格分流，不能用混合字段绕过授权。"""
    if not isinstance(binding, dict):
        raise DomainError(422, "binding_invalid", "请选择数据来源")
    inspection_fields = {"inspection_id", "run_id", "view"}
    if is_inspection_binding(binding):
        allowed = {"source_type", *inspection_fields, "label_field", "value_field", "value_fields", "secondary_value_fields"}
        if set(binding) - allowed or any(not isinstance(binding.get(key), str) or not 1 <= len(binding[key]) <= 100 for key in ("inspection_id", "run_id")) or not isinstance(binding.get("view"), str) or binding["view"] not in VIEWS:
            raise DomainError(422, "inspection_binding_invalid", "检验来源或数据栏目无效")
    elif inspection_fields.intersection(binding) or binding.get("source_type") not in (None, "query") or not isinstance(binding.get("query_id"), str) or not 1 <= len(binding["query_id"]) <= 100:
        raise DomainError(422, "binding_invalid", "查询与检验来源不能混用")


class InspectionSource:
    """所有方法只读平台实体和已登记查询，不执行数据源查询或外部请求。"""

    def __init__(self, session, actor, settings=None):
        self.session, self.actor, self.settings = session, actor, settings

    def load(self, inspection_id, run_id):
        self.actor.require("m11.read")
        inspection = self.session.scalar(select(manufacturing.Inspection).where(
            manufacturing.Inspection.id == inspection_id,
            manufacturing.Inspection.workspace_id == self.actor.workspace_id,
            manufacturing.Inspection.archived.is_(False)))
        run = self.session.scalar(select(manufacturing.InspectionRun).where(
            manufacturing.InspectionRun.id == run_id,
            manufacturing.InspectionRun.inspection_id == inspection_id,
            manufacturing.InspectionRun.workspace_id == self.actor.workspace_id))
        if inspection is None or run is None:
            raise DomainError(404, "inspection_run_not_found", "检验运行不存在或不在当前工作区")
        task = self.session.scalar(select(Task).where(Task.id == run.task_id, Task.workspace_id == self.actor.workspace_id))
        if task is None or task.kind != "inspection" or task.inputs.get("inspection_id") != inspection.id or run.result.get("execution_task_id") != task.id or run.result.get("run_id") != run.id:
            raise DomainError(422, "inspection_run_evidence_invalid", "检验运行缺少完整执行证据")
        if run.result.get("standard_revision") != inspection.template_revision or task.inputs.get("template_revision") != inspection.template_revision or task.inputs.get("template_id") != inspection.template_id:
            raise DomainError(422, "inspection_standard_evidence_mismatch", "检验运行与固定标准版本不一致")
        # 必须核验选中运行的原始查询；当前任务后来取过别的源不能替旧证据授权。
        queries = {metric.get("provenance", {}).get("query_id")
                   for sample in run.result.get("samples", []) for metric in sample.get("metrics", [])}
        data = DataService(self.session, self.actor, self.settings)
        for query_id in sorted(value for value in queries if value):
            try:
                data.get_query(query_id)
            except ValidationError as exc:
                # 原始SQL曾通过授权；当前字段或表消失代表当前访问已失效，目录可据403安全过滤。
                if isinstance(exc, SqlFieldValidationError) or exc.fields.get("table"):
                    raise DomainError(403, "inspection_evidence_access_denied", "当前账号无法访问检验的原始数据") from exc
                raise
        return inspection, run

    def reviews(self, inspection, run):
        """复核归属以执行任务事件中的review_id为准，不按时间猜测所属运行。"""
        events = self.session.scalars(select(TaskEvent).where(TaskEvent.task_id == run.task_id, TaskEvent.type == "inspection_reviewed").order_by(TaskEvent.sequence)).all()
        output = []
        for event in events:
            review = self.session.scalar(select(manufacturing.InspectionReview).where(
                manufacturing.InspectionReview.id == event.data.get("review_id"),
                manufacturing.InspectionReview.inspection_id == inspection.id))
            if review is None or event.data.get("outcome") != review.outcome or event.data.get("kind") != review.kind:
                raise DomainError(422, "inspection_review_evidence_invalid", "复核记录与执行事件不一致")
            output.append({"id": review.id, "outcome": review.outcome, "note": review.note,
                           "kind": review.kind, "created_by": review.created_by,
                           "created_at": timestamp(review.created_at), "event_sequence": event.sequence})
        return output

    def capture(self, inspection_id, run_id, expected_revision=None):
        inspection, run = self.load(inspection_id, run_id)
        if expected_revision is not None and inspection.revision != expected_revision:
            raise DomainError(409, "inspection_revision_conflict", "检验任务已更新，请刷新后生成")
        reviews = self.reviews(inspection, run)
        reviewer_names = {}
        for review in reviews:
            # 归属已由同工作区运行的复核事件确认，只投影用于辨认复核人的公开名称。
            user = self.session.get(User, review["created_by"])
            if user is not None:
                reviewer_names[user.id] = user.display_name or user.username
        metadata = {"inspection_name": inspection.name, "target_type": inspection.target_type,
                    "device_name": "", "product_name": "", "batch_number": "",
                    "template_name": inspection.standard_snapshot.get("name", ""),
                    "calculated_at": run.result.get("calculated_at", timestamp(run.created_at)),
                    "system_outcome": run.result.get("outcome", "incomplete")}
        for field, model, row_id in (("device_name", manufacturing.Device, inspection.device_id),
                                     ("product_name", manufacturing.Product, inspection.product_id),
                                     ("batch_number", manufacturing.ProductBatch, inspection.batch_id)):
            if row_id:
                entity = self.session.scalar(select(model).where(model.id == row_id, model.workspace_id == self.actor.workspace_id))
                if entity is not None:
                    metadata[field] = (entity.external_ref or entity.name) if field == "batch_number" else entity.name
        return {"source_type": "inspection_run", "projection_version": 1,
                "inspection_id": inspection.id, "run_id": run.id, "task_id": run.task_id,
                "inspection_revision": inspection.revision,
                "standard_revision": inspection.template_revision,
                "standard_snapshot": deepcopy(inspection.standard_snapshot),
                "result_hash": digest(run.result),
                "review_event_sequence": reviews[-1]["event_sequence"] if reviews else 0,
                "review_snapshot": reviews, "reviewer_names": reviewer_names, "metadata": metadata,
                "captured_at": datetime.now(timezone.utc).isoformat()}

    def authorize(self, snapshot, *, current=False):
        """历史版本核验冻结事实和当前授权；发布另外要求来源版本仍然最新。"""
        if not isinstance(snapshot, dict) or snapshot.get("projection_version") != 1:
            raise DomainError(422, "inspection_source_invalid", "检验报告来源无效")
        inspection, run = self.load(snapshot.get("inspection_id"), snapshot.get("run_id"))
        if snapshot.get("task_id") != run.task_id or snapshot.get("result_hash") != digest(run.result) or snapshot.get("standard_revision") != inspection.template_revision or snapshot.get("standard_snapshot") != inspection.standard_snapshot:
            raise DomainError(422, "inspection_source_evidence_mismatch", "报告来源与检验运行证据不一致")
        reviews = self.reviews(inspection, run)
        cutoff = snapshot.get("review_event_sequence")
        if not isinstance(cutoff, int) or cutoff < 0 or [row for row in reviews if row["event_sequence"] <= cutoff] != snapshot.get("review_snapshot") or (cutoff and not any(row["event_sequence"] == cutoff for row in reviews)):
            raise DomainError(422, "inspection_review_evidence_mismatch", "报告复核快照与执行证据不一致")
        if current:
            sequence = reviews[-1]["event_sequence"] if reviews else 0
            if inspection.revision != snapshot.get("inspection_revision") or inspection.result.get("run_id") != run.id or sequence != cutoff:
                raise DomainError(409, "report_source_changed", "检验或复核已更新，请刷新报告后发布")
            if not reviews:
                raise DomainError(422, "inspection_review_required", "该次检验尚未复核")
        return run

    def rows(self, snapshot, view, *, run=None):
        """投影只读已判定结果；不补零、不重算阈值、不混入其他运行复核。"""
        run = run or self.authorize(snapshot)
        result = run.result
        reviews = snapshot["review_snapshot"]
        if view == "meta":
            values = {**snapshot["metadata"], "standard_revision": snapshot["standard_revision"],
                      "review_status": "已复核" if reviews else "待复核",
                      "review_outcome": reviews[-1]["outcome"] if reviews else None,
                      "captured_at": snapshot["captured_at"]}
            return [{"name": key, "value": value} for key, value in values.items()]
        if view in ("summary", "outcomes"):
            counts = Counter(row.get("outcome", "incomplete") for row in result.get("samples", []))
            if view == "outcomes":
                return [{"name": label, "value": counts.get(key, 0)} for key, label in OUTCOMES.items()]
            values = {"expected_samples": result.get("expected_samples"), "actual_samples": result.get("actual_samples"),
                      "missing_samples": result.get("missing_samples"), **{key + "_samples": counts.get(key, 0) for key in OUTCOMES}}
            return [{"name": key, "value": value} for key, value in values.items()]
        if view == "reviews":
            return deepcopy(reviews)
        standard = snapshot["standard_snapshot"].get("metrics", [])
        if view == "standard":
            return [{"metric_id": metric.get("id"), "name": metric.get("name"), "category": metric.get("category"),
                     "standard_min": metric.get("standard_min"), "standard_max": metric.get("standard_max"),
                     "unit": metric.get("unit"), "component_name": metric.get("component_name"),
                     "minimum_confidence": metric.get("minimum_confidence"),
                     "blocking_severities": ", ".join(metric.get("blocking_severities", []))} for metric in standard]
        if view != "metrics":
            raise DomainError(422, "inspection_view_invalid", "检验数据栏目无效")
        by_id = {metric.get("id"): metric for metric in standard}
        output = []
        for index, sample in enumerate(result.get("samples", []), start=1):
            for metric in sample.get("metrics", []):
                original = by_id.get(metric.get("metric_id"), {})
                observation = metric.get("observation") or {}
                output.append({"sample": sample.get("external_ref") or f"样本 {index}",
                               "metric": metric.get("name"), "category": metric.get("category"),
                               "value": observation.get("value"), "unit": observation.get("unit"),
                               "standard_min": original.get("standard_min"), "standard_max": original.get("standard_max"),
                               "standard_unit": original.get("unit"), "component_name": original.get("component_name"),
                               "minimum_confidence": original.get("minimum_confidence"),
                               "blocking_severities": ", ".join(original.get("blocking_severities", [])),
                               "outcome": metric.get("outcome"), "reason": metric.get("reason"),
                               "observation_note": observation.get("note"),
                               "defects": json.dumps(observation.get("defects", []), ensure_ascii=False) if observation.get("defects") else "",
                               "image_ref": observation.get("image_ref")})
        return output

    def prepare(self, layout, *, previous=None, refresh=False):
        """客户端来源上下文一律移除；仅保留服务端旧快照或重新捕获原始检验事实。"""
        submitted_snapshot = layout.get("inspection_source")
        layout.pop("inspection_source", None)
        bindings = [widget["binding"] for widget in layout.get("widgets", []) if is_inspection_binding(widget.get("binding"))]
        pairs = {(binding["inspection_id"], binding["run_id"]) for binding in bindings}
        previous_snapshot = (previous or {}).get("inspection_source")
        residual = [widget for widget in layout.get("widgets", [])
                    if (widget.get("data_provenance") or {}).get("type") == "inspection_run" and not is_inspection_binding(widget.get("binding"))]
        if (submitted_snapshot and not pairs and not previous_snapshot) or residual:
            # 新建/另存不能通过删掉绑定把已有检验数据洗成无权限依赖的静态文本或模板。
            raise DomainError(422, "inspection_bindings_required", "检验数据副本必须保留检验来源绑定")
        if not pairs:
            # 删除图表不能消除既有敏感来源的权限依赖。
            if previous_snapshot:
                self.authorize(previous_snapshot)
                layout["inspection_source"] = deepcopy(previous_snapshot)
            return layout
        if len(pairs) != 1:
            raise DomainError(422, "inspection_sources_mixed", "一份检验报告只能引用同一次检验运行")
        inspection_id, run_id = next(iter(pairs))
        if previous_snapshot and (previous_snapshot["inspection_id"], previous_snapshot["run_id"]) != (inspection_id, run_id):
            # 不能把A运行留下的静态内容连同B绑定保存后仅核验B；更换检验运行应生成新报告。
            raise DomainError(422, "inspection_source_change_requires_generation", "更换检验运行请生成新报告")
        if previous_snapshot and (previous_snapshot["inspection_id"], previous_snapshot["run_id"]) == (inspection_id, run_id) and not refresh:
            snapshot = deepcopy(previous_snapshot)
        else:
            snapshot = self.capture(inspection_id, run_id)
        run = self.authorize(snapshot)
        layout["inspection_source"] = snapshot
        for widget in layout["widgets"]:
            binding = widget.get("binding") or {}
            if not is_inspection_binding(binding):
                continue
            rows = self.rows(snapshot, binding["view"], run=run)
            widget["data"] = rows
            widget["data_provenance"] = {"type": "inspection_run", "inspection_id": inspection_id, "run_id": run_id,
                                         "view": binding["view"], "result_hash": snapshot["result_hash"], "truncated": False}
            widget.setdefault("config", {}).update(dataStatus="bound", referenceSample=False, chartDataParsed=deepcopy(rows))
        if len(json.dumps(layout, ensure_ascii=False)) > 2_000_000:
            raise DomainError(422, "layout_too_large", "检验报告内容过大，请缩小检验范围")
        return layout

    def validate(self, layout, *, current=False):
        snapshot = layout.get("inspection_source")
        bindings = [widget for widget in layout.get("widgets", []) if is_inspection_binding(widget.get("binding"))]
        if snapshot is None:
            if bindings or layout.get("kind") == "inspection_report":
                raise DomainError(422, "inspection_source_missing", "报告缺少可信检验来源")
            return
        run = self.authorize(snapshot, current=current)
        for widget in bindings:
            binding, provenance = widget["binding"], widget.get("data_provenance") or {}
            expected = {"type": "inspection_run", "inspection_id": snapshot["inspection_id"], "run_id": snapshot["run_id"],
                        "view": binding["view"], "result_hash": snapshot["result_hash"], "truncated": False}
            if binding["inspection_id"] != snapshot["inspection_id"] or binding["run_id"] != snapshot["run_id"] or provenance != expected or widget.get("data") != self.rows(snapshot, binding["view"], run=run):
                raise DomainError(422, "report_data_evidence_mismatch", "报告内容与检验运行证据不一致", {"widget_id": widget["id"]})
