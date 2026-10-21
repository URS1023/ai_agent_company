"""制造领域事务服务：工作区隔离、乐观锁、标准版本与追加证据。"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
import csv

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from pydantic import ValidationError

from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.core.records import RecordStore
from agent_platform.core.tasks import TaskService

from . import models as m
from .assessment import assess, overall, has_missing_evidence
from .maintenance import analyze
from .schemas import AlertEvaluation, AnalysisInput, Metric, Observation


def view(row) -> dict:
    """显式ORM列投影，避免关系对象和会话状态进入API。"""
    result = {}
    for column in row.__table__.columns:
        key = "metadata_json" if column.name == "metadata_json" else column.name
        value = getattr(row, key)
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        result["metadata" if key == "metadata_json" else key] = value.isoformat() if isinstance(value, datetime) else value
    return result


class ManufacturingService:
    def __init__(self, session: Session, actor: Actor):
        self.session, self.actor = session, actor

    def audit(self, action, resource, row_id, details=None):
        RecordStore(self.session, self.actor).audit(action, resource, row_id, details)

    def _execution(self, kind, title, inputs, permission):
        tasks = TaskService(self.session, self.actor)
        task = tasks.create(kind, title, inputs=inputs, access_permission=permission)
        tasks.transition(task["id"], "running")
        return tasks, task["id"]

    def _failed_execution(self, error, kind, title, inputs, permission):
        """业务事务回滚后单独记失败任务，组合已有数据源隔离补偿，不重试计算。"""
        previous = getattr(error, "after_rollback", None)
        actor = self.actor
        code = error.code if isinstance(error, DomainError) else "execution_failed"
        def record_failure(guard_session):
            if callable(previous):
                previous(guard_session)
            tasks = TaskService(guard_session, actor)
            failed = tasks.create(kind, title, inputs=inputs, access_permission=permission)
            tasks.transition(failed["id"], "failed", error={"code": code, "message": "业务执行失败，请查看当前操作反馈"})
            if isinstance(error, DomainError):
                error.fields["execution_task_id"] = failed["id"]
        error.after_rollback = record_failure

    def get(self, model, row_id, *, archived=False):
        query = select(model).where(model.id == row_id, model.workspace_id == self.actor.workspace_id)
        if not archived:
            query = query.where(model.archived.is_(False))
        row = self.session.scalar(query)
        if row is None:
            raise DomainError(404, "resource_not_found", "资源不存在或不在当前工作区")
        self._authorize_evidence(row)
        return row

    def _authorize_evidence(self, row):
        """领域历史结果仍受来源字段的当前授权控制。"""
        from ..data.service import DataService
        queries = set()
        if isinstance(row, m.MaintenanceAnalysis) and row.provenance.get("query_id"):
            queries.add(row.provenance["query_id"])
        if isinstance(row, m.Inspection):
            for sample in row.result.get("samples", []):
                for metric in sample.get("metrics", []):
                    query_id = metric.get("provenance", {}).get("query_id")
                    if query_id:
                        queries.add(query_id)
        if isinstance(row, m.Incident):
            for evidence in (row.evidence, row.evidence.get("first", {}), row.evidence.get("latest", {})):
                query_id = evidence.get("provenance", {}).get("query_id")
                if query_id:
                    queries.add(query_id)
        for query_id in queries:
            try:
                DataService(self.session, self.actor).get_query(query_id)
            except DomainError as exc:
                from ..data.sql_safety import SqlFieldValidationError
                if isinstance(exc, SqlFieldValidationError) or (exc.status == 422 and exc.fields.get("table")):
                    raise DomainError(403, "manufacturing_evidence_access_denied", "当前账号无法访问原始业务证据") from exc
                raise

    def list(self, model, *, q="", limit=50, offset=0, **filters):
        clauses = [model.workspace_id == self.actor.workspace_id, model.archived.is_(False)]
        if q and hasattr(model, "name"):
            clauses.append(model.name.contains(q, autoescape=True))
        for key, value in filters.items():
            if value is not None:
                clauses.append(getattr(model, key) == value)
        statement = select(model).where(*clauses).order_by(model.created_at.desc())
        if model in (m.Inspection, m.Incident, m.MaintenanceAnalysis):
            # 先按当前来源权限过滤再分页，撤权的一条历史记录不能阻断整个工作区或泄露总数。
            visible = []
            for row in self.session.scalars(statement):
                try:
                    self._authorize_evidence(row)
                except DomainError as exc:
                    if exc.status in (403, 404):
                        continue
                    raise
                visible.append(row)
            total = len(visible)
            rows = visible[max(offset, 0):max(offset, 0) + min(max(limit, 1), 200)]
        else:
            total = self.session.scalar(select(func.count()).select_from(model).where(*clauses))
            rows = self.session.scalars(statement.limit(min(max(limit, 1), 200)).offset(max(offset, 0))).all()
        items = self.inspection_views(rows) if model is m.Inspection else self.maintenance_views(rows) if model in (m.Incident, m.MaintenanceAnalysis) else [view(row) for row in rows]
        return {"items": items, "total": total}

    def maintenance_views(self, rows):
        """只投影同工作区设备名称，不为显示名称开放额外模块权限。"""
        ids = {row.device_id for row in rows}
        devices = dict(self.session.execute(select(m.Device.id, m.Device.name).where(m.Device.id.in_(ids), m.Device.workspace_id == self.actor.workspace_id)).all()) if ids else {}
        incidents = {row.id for row in rows if isinstance(row, m.Incident)}
        analyses = {row.id for row in rows if isinstance(row, m.MaintenanceAnalysis)}
        orders = self.session.scalars(select(m.MaintenanceWorkOrder).where(m.MaintenanceWorkOrder.workspace_id == self.actor.workspace_id, m.MaintenanceWorkOrder.archived.is_(False), (m.MaintenanceWorkOrder.incident_id.in_(incidents)) | (m.MaintenanceWorkOrder.analysis_id.in_(analyses)))).all() if incidents or analyses else []
        by_source = {(order.incident_id or order.analysis_id): order.id for order in orders}
        return [{**view(row), "device_name": devices.get(row.device_id), "work_order_id": by_source.get(row.id)} for row in rows]

    def inspection_views(self, rows):
        """为检验读者批量投影当前工作区名称；不开放主数据模块或泄露外工作区引用。"""
        if any(row.workspace_id != self.actor.workspace_id for row in rows):
            raise DomainError(404, "resource_not_found", "资源不存在或不在当前工作区")
        lookups = {}
        for model, field in ((m.Device, "device_id"), (m.Product, "product_id"), (m.ProductBatch, "batch_id"), (m.InspectionTemplate, "template_id")):
            ids = {getattr(row, field) for row in rows if getattr(row, field)}
            columns = (model.id, model.name, model.external_ref) if model is m.ProductBatch else (model.id, model.name)
            matches = self.session.execute(select(*columns).where(model.workspace_id == self.actor.workspace_id, model.id.in_(ids))).all() if ids else []
            lookups[field] = {item[0]: item for item in matches}
        result = []
        for row in rows:
            device = lookups["device_id"].get(row.device_id)
            product = lookups["product_id"].get(row.product_id)
            batch = lookups["batch_id"].get(row.batch_id)
            template = lookups["template_id"].get(row.template_id)
            # 标准固定在任务创建时的版本，后续主标准改名不能改写历史语义。
            snapshot_name = row.standard_snapshot.get("name")
            result.append({**view(row), "device_name": device[1] if device else None,
                           "product_name": product[1] if product else None,
                           "batch_number": batch[1] if batch else None,
                           "batch_external_ref": batch[2] if batch else None,
                           "template_name": snapshot_name or (template[1] if template else None)})
        return result

    def create(self, model, data):
        data = dict(data)
        data.pop("revision", None)
        if data.get("code") and hasattr(model, "code"):
            existing = self.session.scalar(select(model.id).where(model.workspace_id == self.actor.workspace_id, model.code == data["code"], model.archived.is_(False)))
            if existing:
                raise DomainError(409, "duplicate_code", "编号已被使用", {"code": "请使用不同编号"})
        if model is m.MeasurementPoint:
            if self.get(m.Device, data["device_id"]).status != "active":
                raise DomainError(422, "device_inactive", "所选设备已停用")
            if data.get("datasource_id"):
                from ..data.service import DataService
                DataService(self.session, self.actor).get_source(data["datasource_id"])
        if model is m.AlertRule:
            self.get(m.Device, data["device_id"])
            if data.get("point_id"):
                point = self.get(m.MeasurementPoint, data["point_id"])
                if point.device_id != data["device_id"]:
                    raise DomainError(422, "point_device_mismatch", "测点不属于所选设备")
        row = model(workspace_id=self.actor.workspace_id, **data)
        self.session.add(row)
        self.session.flush()
        self.audit("create", model.__tablename__, row.id)
        if model is m.InspectionTemplate:
            self._snapshot(row)
        return view(row)

    def _snapshot(self, template):
        self.session.add(m.TemplateRevision(template_id=template.id, number=template.revision, snapshot=view(template)))
        self.session.flush()

    def change(self, model, row_id, changes, expected_revision):
        row = self.get(model, row_id)
        if expected_revision is None:
            raise DomainError(422, "revision_required", "请提交当前版本号", {"revision": "必填"})
        data = dict(changes)
        data.pop("revision", None)
        if model is m.Inspection and data.get("result") == {} and row.result.get("execution_task_id"):
            tasks = TaskService(self.session, self.actor)
            execution_id = row.result["execution_task_id"]
            tasks.event(execution_id, "evidence_changed", {"inspection_id": row.id, "previous_outcome": row.outcome, "previous_revision": row.revision})
            task = tasks.get(execution_id)
            if task["status"] == "waiting_review":
                tasks.transition(execution_id, "cancelled", outputs={"inspection_id": row.id, "reason": "evidence_changed"})
        if data.get("code") and hasattr(model, "code"):
            duplicate = self.session.scalar(select(model.id).where(model.workspace_id == self.actor.workspace_id, model.code == data["code"], model.id != row.id, model.archived.is_(False)))
            if duplicate:
                raise DomainError(409, "duplicate_code", "编号已被使用", {"code": "请使用不同编号"})
        if model in (m.MeasurementPoint, m.AlertRule):
            device_id = data.get("device_id", row.device_id)
            self.get(m.Device, device_id)
            if model is m.MeasurementPoint and data.get("datasource_id"):
                from ..data.service import DataService
                DataService(self.session, self.actor).get_source(data["datasource_id"])
            if model is m.AlertRule and data.get("point_id"):
                if self.get(m.MeasurementPoint, data["point_id"]).device_id != device_id:
                    raise DomainError(422, "point_device_mismatch", "测点不属于所选设备")
        data["revision"] = expected_revision + 1
        data["updated_at"] = m.now()
        result = self.session.execute(update(model).where(model.id == row.id, model.workspace_id == self.actor.workspace_id, model.revision == expected_revision).values(**data))
        if result.rowcount != 1:
            raise DomainError(409, "revision_conflict", "内容已更新，请刷新后重试")
        self.session.flush()
        self.session.refresh(row)
        self.audit("update", model.__tablename__, row.id)
        if model is m.InspectionTemplate:
            self._snapshot(row)
        return view(row)

    def archive(self, model, row_id, revision):
        row = self.get(model, row_id)
        if model is m.Inspection:
            linked = self.session.scalar(select(m.InspectionReinspection.id).where(m.InspectionReinspection.workspace_id == self.actor.workspace_id, (m.InspectionReinspection.parent_inspection_id == row.id) | (m.InspectionReinspection.child_inspection_id == row.id)).limit(1))
            case = self.session.scalar(select(m.QualityCase.id).where(m.QualityCase.workspace_id == self.actor.workspace_id, m.QualityCase.inspection_id == row.id).limit(1))
            if linked or case:
                raise DomainError(409, "inspection_history_in_use", "该任务关联复检或处置记录，需要保留追溯")
        # 主数据软删保留历史引用；执行中任务引用的设备/产品/标准需先停用任务。
        refs = {m.Device: "device_id", m.Product: "product_id", m.ProductBatch: "batch_id", m.InspectionTemplate: "template_id"}
        if model in refs:
            count = self.session.scalar(select(func.count()).select_from(m.Inspection).where(getattr(m.Inspection, refs[model]) == row.id, m.Inspection.archived.is_(False), m.Inspection.status.in_(["draft", "running"])))
            if count:
                raise DomainError(409, "resource_in_use", "存在未结束的检验任务，请先处理任务")
        return self.change(model, row_id, {"archived": True}, revision)

    def edit_inspection(self, inspection_id, data):
        """任务仅编辑名称与计划样本数，目标和已固定标准通过新任务变更。"""
        row = self.get(m.Inspection, inspection_id)
        if row.status == "reviewed":
            raise DomainError(409, "inspection_closed", "已复核任务不能修改，请新建复检")
        changes = {k: v for k, v in data.items() if k in ("name", "expected_samples")}
        if "expected_samples" in changes:
            changes.update(result={}, outcome="incomplete", status="draft")
        return self.change(m.Inspection, row.id, changes, data["revision"])

    def edit_sample(self, inspection_id, sample_id, data, *, archive=False):
        inspection = self.get(m.Inspection, inspection_id)
        sample = self.get(m.InspectionSample, sample_id)
        if sample.inspection_id != inspection.id:
            raise DomainError(404, "sample_not_found", "当前任务没有该样本")
        if inspection.status == "reviewed":
            raise DomainError(409, "inspection_closed", "已复核任务不能修改样本")
        if not archive and data.get("external_ref"):
            existing = self.session.scalar(select(m.InspectionSample.id).where(m.InspectionSample.inspection_id == inspection.id, m.InspectionSample.id != sample.id, m.InspectionSample.external_ref == data["external_ref"]))
            if existing:
                raise DomainError(409, "sample_exists", "样本编号已被使用")
        values = {"archived": True} if archive else {"external_ref": data["external_ref"], "metadata_json": data.get("metadata", {})}
        result = self.change(m.InspectionSample, sample.id, values, data["revision"])
        updated = self.change(m.Inspection, inspection.id, {"result": {}, "outcome": "incomplete", "status": "draft"}, inspection.revision)
        result["inspection_revision"] = updated["revision"]
        return result

    def create_batch(self, data):
        self.get(m.Product, data["product_id"])
        return self.create(m.ProductBatch, data)

    def create_inspection(self, data):
        template = self.get(m.InspectionTemplate, data["template_id"])
        if template.status != "active" or template.target_type != data["target_type"]:
            raise DomainError(422, "invalid_template", "标准未启用或检验对象类型不匹配")
        snapshot = self.session.scalar(select(m.TemplateRevision).where(m.TemplateRevision.template_id == template.id, m.TemplateRevision.number == data["template_revision"]))
        if snapshot is None:
            raise DomainError(404, "standard_revision_not_found", "标准版本不存在")
        if data["target_type"] == "device":
            if not data.get("device_id") or data.get("product_id") or data.get("batch_id"):
                raise DomainError(422, "invalid_inspection_target", "设备检验须选择设备")
            if self.get(m.Device, data["device_id"]).status != "active":
                raise DomainError(422, "device_inactive", "所选设备已停用")
        else:
            if not data.get("product_id") or data.get("device_id"):
                raise DomainError(422, "invalid_inspection_target", "产品检验须选择产品")
            if self.get(m.Product, data["product_id"]).status != "active":
                raise DomainError(422, "product_inactive", "所选产品已停用")
            if data.get("batch_id") and self.get(m.ProductBatch, data["batch_id"]).product_id != data["product_id"]:
                raise DomainError(422, "batch_product_mismatch", "批次不属于所选产品")
        return self.create(m.Inspection, {**data, "standard_snapshot": snapshot.snapshot})

    def add_sample(self, inspection_id, data):
        inspection = self.get(m.Inspection, inspection_id)
        if inspection.status == "reviewed":
            raise DomainError(409, "inspection_closed", "已复核任务不能追加样本，请新建复检任务")
        existing = self.session.scalar(select(m.InspectionSample).where(m.InspectionSample.inspection_id == inspection.id, m.InspectionSample.external_ref == data["external_ref"]))
        if existing:
            raise DomainError(409, "sample_exists", "该样本编号已登记")
        self.change(m.Inspection, inspection.id, {"status": "draft", "outcome": "incomplete", "result": {}}, inspection.revision)
        data = {**data, "inspection_id": inspection.id}
        data["metadata_json"] = data.pop("metadata", {})
        return {**self.create(m.InspectionSample, data), "inspection_revision": inspection.revision}

    def add_evidence(self, inspection_id, data):
        inspection = self.get(m.Inspection, inspection_id)
        sample = self.get(m.InspectionSample, data["sample_id"])
        if sample.inspection_id != inspection.id:
            raise DomainError(422, "sample_task_mismatch", "样本不属于当前任务")
        if inspection.status == "reviewed":
            raise DomainError(409, "inspection_closed", "已复核任务不能覆盖证据，请新建复检任务")
        allowed = {metric["id"] for metric in inspection.standard_snapshot["metrics"]}
        observations = [Observation.model_validate(o).model_dump() for o in data["observations"]]
        if len({o["metric_id"] for o in observations}) != len(observations) or any(o["metric_id"] not in allowed for o in observations):
            raise DomainError(422, "invalid_metric", "存在重复指标或标准外指标")
        provenance = {"type": "manual", "actor_id": self.actor.user_id}
        if data.get("query_id"):
            from ..data.service import DataService
            query = DataService(self.session, self.actor).get_query(data["query_id"])
            # 数据库证据必须来自服务端查询快照，不接受把人工值贴上查询标签。
            mapped = query.result.get("rows", [])
            for observation in observations:
                if not any(str(r.get("metric_id")) == observation["metric_id"] and str(r.get("value")) == str(observation.get("value")) and str(r.get("unit", "")) == observation["unit"] for r in mapped):
                    raise DomainError(422, "evidence_source_mismatch", "观测结果与所引用查询不一致")
            provenance = {"type": "query", "query_id": query.id, "datasource_id": query.datasource_id}
        self.change(m.Inspection, inspection.id, {"status": "draft", "outcome": "incomplete", "result": {}}, data["revision"])
        row = m.SampleEvidence(inspection_id=inspection.id, sample_id=sample.id, observations=observations, provenance=provenance, created_by=self.actor.user_id)
        self.session.add(row)
        self.session.flush()
        self.audit("append_evidence", "inspection", inspection.id, {"sample_id": sample.id, "evidence_id": row.id})
        return {"evidence": view(row), "inspection_revision": inspection.revision}

    def import_inspection(self, inspection_id, config, settings):
        """受控重跑查询并映射到任务下样本；实测证据保留查询与字段映射。"""
        from ..data.service import DataService
        import json
        inspection = self.get(m.Inspection, inspection_id)
        if inspection.revision != config.revision:
            raise DomainError(409, "revision_conflict", "任务已更新，请刷新后重试")
        if inspection.status == "reviewed":
            raise DomainError(409, "inspection_closed", "已复核任务不能补充证据，请新建复检")
        data_service = DataService(self.session, self.actor, settings)
        saved = data_service.get_query(config.query_id)
        query = data_service.execute_query({"datasource_id": saved.datasource_id, "sql": saved.sql, "parameters": saved.parameters, "max_rows": 10000})
        if query["truncated"]:
            raise DomainError(422, "inspection_source_truncated", "查询已截断，请缩小范围")
        allowed = {item["id"] for item in inspection.standard_snapshot["metrics"]}
        if any(mapping.metric_id not in allowed for mapping in config.mappings) or len({v.metric_id for v in config.mappings}) != len(config.mappings):
            raise DomainError(422, "invalid_metric", "映射包含重复或标准外指标")
        required = {config.sample_field}
        for mapping in config.mappings:
            required.update(value for key, value in mapping.model_dump().items() if key.endswith("_field") and value)
        if not required.issubset(query["columns"]):
            raise DomainError(422, "mapping_field_missing", "映射字段不在查询结果中", {"fields": sorted(required - set(query["columns"]))})
        prepared, references = [], set()
        for row in query["rows"]:
            reference = str(row[config.sample_field] or "").strip()
            if not reference or len(reference) > 160 or reference in references:
                raise DomainError(422, "ambiguous_sample_mapping", "样本编号为空或重复；每个样本须在查询结果中占一行")
            references.add(reference)
            observations = []
            for mapping in config.mappings:
                defects = row.get(mapping.defects_field) if mapping.defects_field else None
                if isinstance(defects, str):
                    try:
                        defects = json.loads(defects)
                    except ValueError as exc:
                        raise DomainError(422, "defects_json_invalid", "外观结果字段须为JSON数组") from exc
                raw = row.get(mapping.value_field) if mapping.value_field else None
                if isinstance(raw, str) and not raw.strip():
                    raw = None
                try:
                    observation = Observation(metric_id=mapping.metric_id, value=str(raw) if raw is not None else None,
                                              unit=str(row.get(mapping.unit_field, "")) if mapping.unit_field else mapping.unit,
                                              image_ref=str(row.get(mapping.image_ref_field) or "") if mapping.image_ref_field else "",
                                              defects=defects)
                except ValidationError as exc:
                    raise DomainError(422, "source_observation_invalid", "来源数据无法映射为有效观测", {"sample": reference, "metric_id": mapping.metric_id}) from exc
                observations.append(observation.model_dump())
            prepared.append((reference, observations))
        self.change(m.Inspection, inspection.id, {"status": "draft", "outcome": "incomplete", "result": {}}, config.revision)
        sample_ids = []
        for reference, observations in prepared:
            sample = self.session.scalar(select(m.InspectionSample).where(m.InspectionSample.inspection_id == inspection.id, m.InspectionSample.external_ref == reference, m.InspectionSample.archived.is_(False)))
            if sample is None:
                sample = m.InspectionSample(workspace_id=self.actor.workspace_id, inspection_id=inspection.id, external_ref=reference, metadata_json={"query_id": query["id"]})
                self.session.add(sample)
                self.session.flush()
            sample_ids.append(sample.id)
            self.session.add(m.SampleEvidence(inspection_id=inspection.id, sample_id=sample.id,
                                             observations=observations, provenance={"type": "query", "query_id": query["id"], "datasource_id": saved.datasource_id, "mappings": [v.model_dump() for v in config.mappings]}, created_by=self.actor.user_id))
        self.session.flush()
        self.audit("import_evidence", "inspection", inspection.id, {"query_id": query["id"], "samples": len(prepared)})
        return {"inspection_id": inspection.id, "revision": inspection.revision, "query_id": query["id"], "sample_ids": sample_ids, "imported_samples": len(prepared)}

    def telemetry(self, device_id, settings, limit=500):
        """按设备测点的受控SQL映射读取，返回来源而不生成假遥测。"""
        from ..data.service import DataService
        self.get(m.Device, device_id)
        points = self.session.scalars(select(m.MeasurementPoint).where(m.MeasurementPoint.workspace_id == self.actor.workspace_id, m.MeasurementPoint.device_id == device_id, m.MeasurementPoint.archived.is_(False), m.MeasurementPoint.status == "active")).all()
        items = []
        for point in points:
            if not point.datasource_id or not point.mapping.get("sql"):
                continue
            query = DataService(self.session, self.actor, settings).execute_query({"datasource_id": point.datasource_id, "sql": point.mapping["sql"], "parameters": point.mapping.get("parameters", {}), "max_rows": min(limit, 1000)})
            items.append({"point_id": point.id, "name": point.name, "unit": point.unit, "query_id": query["id"], "columns": query["columns"], "rows": query["rows"], "truncated": query["truncated"]})
        return {"items": items, "total": len(items), "mapped_points": len(items), "unmapped_points": len(points) - len(items)}

    def run_inspection(self, inspection_id, revision):
        inspection = self.get(m.Inspection, inspection_id)
        if inspection.revision != revision:
            raise DomainError(409, "revision_conflict", "内容已更新，请刷新后重试")
        if inspection.status == "reviewed":
            raise DomainError(409, "inspection_closed", "已复核任务不能重新计算")
        inputs = {"inspection_id": inspection.id, "revision": revision, "template_id": inspection.template_id, "template_revision": inspection.template_revision}
        tasks, task_id = self._execution("inspection", inspection.name, inputs, "m11.read")
        try:
            return self._calculate_inspection(inspection, revision, tasks, task_id)
        except Exception as exc:
            self._failed_execution(exc, "inspection", inspection.name, inputs, "m11.read")
            raise

    def _calculate_inspection(self, inspection, revision, tasks, task_id):
        """单次检测计算与任务投影同事务；固定证据结果另存不可变运行记录。"""
        samples = self.session.scalars(select(m.InspectionSample).where(m.InspectionSample.inspection_id == inspection.id, m.InspectionSample.archived.is_(False)).order_by(m.InspectionSample.created_at)).all()
        metrics = [Metric.model_validate(v) for v in inspection.standard_snapshot["metrics"]]
        outputs = []
        for sample in samples:
            evidence = self.session.scalar(select(m.SampleEvidence).where(m.SampleEvidence.sample_id == sample.id).order_by(m.SampleEvidence.created_at.desc(), m.SampleEvidence.id.desc()).limit(1))
            observations = {o["metric_id"]: Observation.model_validate(o) for o in evidence.observations} if evidence else {}
            results = [assess(metric, observations.get(metric.id), evidence.provenance if evidence else {}) for metric in metrics]
            outputs.append({"sample_id": sample.id, "external_ref": sample.external_ref, "evidence_id": evidence.id if evidence else None, "outcome": overall([r["outcome"] for r in results]), "metrics": results})
        outcomes = [o["outcome"] for o in outputs]
        missing = max(inspection.expected_samples - len(samples), 0)
        if missing:
            outcomes.append("incomplete")
        outcome = overall(outcomes)
        result = {"samples": outputs, "missing_samples": missing, "expected_samples": inspection.expected_samples,
                  "actual_samples": len(samples), "standard_revision": inspection.template_revision,
                  "outcome": outcome, "calculated_at": m.now().isoformat(), "execution_task_id": task_id}
        run = m.InspectionRun(workspace_id=self.actor.workspace_id, inspection_id=inspection.id, task_id=task_id, result=result)
        self.session.add(run)
        self.session.flush()
        # 替换JSON对象，避免原地改动使ORM漏掉运行编号，导致历史重读丢失证据。
        result = {**result, "run_id": run.id}
        run.result = dict(result)
        response = self.change(m.Inspection, inspection.id, {"result": result, "outcome": outcome, "status": "awaiting_review"}, revision)
        tasks.transition(task_id, "waiting_review", outputs={"inspection_id": inspection.id, "run_id": run.id, "outcome": outcome, "result_url": f"/api/v1/inspections/{inspection.id}/results"})
        return response

    def review(self, inspection_id, data, *, kind="review"):
        """追加复核与任务事件；合格复核须有完整样本、指标证据，拒绝时不写入。"""
        inspection = self.get(m.Inspection, inspection_id)
        if not inspection.result:
            raise DomainError(409, "inspection_not_run", "请先完成检验计算")
        if data["outcome"] == "passed" and has_missing_evidence(inspection.result):
            raise DomainError(422, "missing_evidence", "证据不完整，不能复核为合格")
        result = self.change(m.Inspection, inspection.id, {"outcome": data["outcome"], "status": "reviewed"}, data["revision"])
        review = m.InspectionReview(inspection_id=inspection.id, outcome=data["outcome"], note=data["note"], kind=kind, created_by=self.actor.user_id)
        self.session.add(review)
        self.session.flush()
        execution_id = inspection.result.get("execution_task_id")
        if execution_id:
            tasks = TaskService(self.session, self.actor)
            task = tasks.get(execution_id)
            tasks.event(execution_id, "inspection_reviewed", {"review_id": review.id, "outcome": data["outcome"], "kind": kind})
            if task["status"] == "waiting_review":
                tasks.transition(execution_id, "succeeded", outputs={"inspection_id": inspection.id, "review_id": review.id, "outcome": data["outcome"]})
        return {"inspection": result, "review": view(review)}

    def inspection_results(self, inspection_id):
        row = self.get(m.Inspection, inspection_id)
        reviews = self.session.scalars(select(m.InspectionReview).where(m.InspectionReview.inspection_id == row.id).order_by(m.InspectionReview.created_at)).all()
        from .workflow import people_names
        names = people_names(self.session, self.actor, [review.created_by for review in reviews])
        return {"id": row.id, "revision": row.revision, "outcome": row.outcome, "result": row.result,
                "standard_snapshot": row.standard_snapshot, "reviews": [{**view(review), "reviewer_name": names.get(review.created_by)} for review in reviews]}

    def create_analysis(self, config: AnalysisInput, settings):
        self.get(m.Device, config.device_id)
        if config.rows is not None and (config.datasource_id or config.sql):
            raise DomainError(422, "ambiguous_source", "导入观测和数据库查询须选择一种")
        if config.rows is None and not (config.datasource_id and config.sql):
            raise DomainError(422, "analysis_source_required", "请选择受控数据源查询")
        inputs = {"device_id": config.device_id, "datasource_id": config.datasource_id, "source_type": "query" if config.datasource_id else "imported_observations"}
        tasks, task_id = self._execution("maintenance_analysis", config.name, inputs, "m10.read")
        try:
            result = self._calculate_analysis(config, settings)
            row = self.get(m.MaintenanceAnalysis, result["id"])
            row.result = {**row.result, "execution_task_id": task_id}
            self.session.flush()
            tasks.transition(task_id, "succeeded", outputs={"analysis_id": row.id, "business_status": row.status, "coverage": row.result["coverage"], "result_url": f"/api/v1/maintenance-analyses/{row.id}"})
            return view(row)
        except Exception as exc:
            self._failed_execution(exc, "maintenance_analysis", config.name, inputs, "m10.read")
            raise

    def _calculate_analysis(self, config, settings):
        """从明确来源取数后比较，数据不足也保留真实计算结果。"""
        if config.rows is not None:
            rows, provenance = config.rows, {"type": "imported_observations", "actor_id": self.actor.user_id}
        elif config.datasource_id and config.sql:
            from ..data.service import DataService
            query = DataService(self.session, self.actor, settings).execute_query({"datasource_id": config.datasource_id, "sql": config.sql, "parameters": config.parameters, "max_rows": 10000})
            if query["result"]["truncated"]:
                raise DomainError(422, "analysis_source_truncated", "分析数据超过预算，请缩小时间范围")
            rows = query["result"]["rows"]
            provenance = {"type": "query", "query_id": query["id"], "datasource_id": config.datasource_id}
        result = analyze(rows, config)
        request = config.model_dump(exclude={"rows"})
        return self.create(m.MaintenanceAnalysis, {"name": config.name, "device_id": config.device_id, "status": result["status"], "request_snapshot": request, "result": result, "provenance": provenance})

    def evaluate_alert(self, rule_id, data, *, provenance=None):
        rule = self.get(m.AlertRule, rule_id)
        # PostgreSQL部署按规则行串行去重；SQLite源查询已在同事务取得写锁，失败不重试。
        self.session.scalar(select(m.AlertRule).where(m.AlertRule.id == rule.id, m.AlertRule.workspace_id == self.actor.workspace_id).with_for_update())
        if rule.status != "active":
            raise DomainError(409, "rule_inactive", "预警规则未启用")
        if data["unit"] != rule.unit:
            raise DomainError(422, "unit_mismatch", "实测单位与规则单位不一致")
        try:
            observed = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError()
            observed = observed.astimezone(timezone.utc)
        except ValueError as exc:
            raise DomainError(422, "invalid_time", "观测时间须包含时区") from exc
        value, threshold = Decimal(data["value"]), Decimal(rule.threshold)
        matched = {"gt": value > threshold, "gte": value >= threshold, "lt": value < threshold, "lte": value <= threshold}[rule.operator]
        result = {"matched": matched, "rule_id": rule.id, "rule_revision": rule.revision, "operator": rule.operator, "value": data["value"], "threshold": rule.threshold, "unit": rule.unit, "observed_at": observed.isoformat(), "provenance": provenance or {"type": "manual", "actor_id": self.actor.user_id}}
        if not matched or not data.get("create_incident"):
            return result
        previous = self.session.scalar(select(m.Incident).where(m.Incident.workspace_id == self.actor.workspace_id, m.Incident.rule_id == rule.id, m.Incident.status.in_(["open", "acknowledged", "assigned", "in_progress", "awaiting_review"]), m.Incident.last_observed_at.between(observed - timedelta(minutes=rule.dedupe_minutes), observed + timedelta(minutes=rule.dedupe_minutes))).order_by(m.Incident.created_at.desc()).limit(1))
        if previous:
            first = previous.evidence.get("first", previous.evidence)
            # 改过判定条件或历史缺少标准快照时新建预警，不能合并后再用新阈值结旧案。
            if any(first.get(key) != result[key] for key in ("operator", "threshold", "unit")):
                previous = None
        if previous:
            last = previous.last_observed_at.replace(tzinfo=timezone.utc) if previous.last_observed_at.tzinfo is None else previous.last_observed_at
            latest_evidence = previous.evidence.get("latest", previous.evidence)
            if last == observed and latest_evidence.get("value") == data["value"]:
                return {**result, "incident": view(previous), "deduplicated": True, "same_observation": True}
            evidence = {"first": previous.evidence.get("first", previous.evidence), "latest": result.copy()}
            self.change(m.Incident, previous.id, {"last_observed_at": max(last, observed), "occurrences": previous.occurrences + 1, "evidence": evidence}, previous.revision)
            result.update(incident=view(previous), deduplicated=True)
        else:
            incident = self.create(m.Incident, {"rule_id": rule.id, "device_id": rule.device_id, "name": rule.name, "first_observed_at": observed, "last_observed_at": observed, "evidence": result.copy()})
            result.update(incident=incident, deduplicated=False)
        import json
        self.session.add(m.IncidentEvent(incident_id=result["incident"]["id"], action="observation", note=json.dumps({"observed_at": observed.isoformat(), "provenance": result["provenance"]}, ensure_ascii=False), actor_id=self.actor.user_id))
        self.session.flush()
        return result

    def evaluate_source_alert(self, rule_id, config, settings):
        """重跑受控查询，选取有时区时间的最新观测，不接收客户端伪造数值。"""
        from ..data.service import DataService
        rule = self.get(m.AlertRule, rule_id)
        data = DataService(self.session, self.actor, settings)
        saved = data.get_query(config.query_id)
        query = data.execute_query({"datasource_id": saved.datasource_id, "sql": saved.sql, "parameters": saved.parameters, "max_rows": 10000})
        if query["truncated"]:
            raise DomainError(422, "alert_source_truncated", "预警数据已截断，请缩小查询范围")
        required = {config.value_field, config.time_field} | ({config.unit_field} if config.unit_field else set())
        if not required.issubset(query["columns"]) or not query["rows"]:
            raise DomainError(422, "alert_source_missing", "预警查询缺少所需字段或有效观测")
        observations = []
        for row in query["rows"]:
            try:
                stamp = datetime.fromisoformat(str(row[config.time_field]).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError()
                observations.append((stamp.astimezone(timezone.utc), row))
            except (ValueError, TypeError) as exc:
                raise DomainError(422, "alert_source_time_invalid", "来源时间须完整且包含时区") from exc
        stamp, latest = max(observations, key=lambda item: item[0])
        unit = str(latest[config.unit_field]) if config.unit_field else config.unit
        if unit is None and rule.point_id:
            unit = self.get(m.MeasurementPoint, rule.point_id).unit
        if unit is None:
            raise DomainError(422, "alert_source_unit_required", "请映射来源单位或选择已配置单位的测点")
        try:
            values = AlertEvaluation(value=str(latest[config.value_field]), unit=unit, observed_at=stamp.isoformat(), create_incident=config.create_incident).model_dump()
        except ValidationError as exc:
            raise DomainError(422, "alert_source_value_invalid", "来源数据不是有效的精确数值") from exc
        return self.evaluate_alert(rule.id, values, provenance={"type": "query", "query_id": query["id"], "datasource_id": saved.datasource_id, "value_field": config.value_field, "time_field": config.time_field})

    def incident_action(self, incident_id, data):
        incident = self.get(m.Incident, incident_id)
        if data["action"] in ("resolve", "reopen"):
            raise DomainError(409, "maintenance_work_order_required", "请通过维修工单记录维修并完成复核")
        transitions = {("open", "acknowledge"): "acknowledged"}
        status = incident.status if data["action"] == "comment" else transitions.get((incident.status, data["action"]))
        if status is None:
            raise DomainError(409, "invalid_transition", "当前状态不支持该操作")
        result = self.change(m.Incident, incident.id, {"status": status}, data["revision"])
        self.session.add(m.IncidentEvent(incident_id=incident.id, action=data["action"], note=data["note"], actor_id=self.actor.user_id))
        self.session.flush()
        return result


def csv_content(rows: list[dict]) -> str:
    """真实CSV导出并防止电子表格公式注入，嵌套值以JSON表示。"""
    import json
    out = StringIO(newline="")
    columns = list(dict.fromkeys(k for row in rows for k in row))
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        clean = {}
        for key, value in row.items():
            value = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value) if value is not None else ""
            clean[key] = "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
        writer.writerow(clean)
    return "\ufeff" + out.getvalue()
