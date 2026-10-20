"""维护工单复用既有预警、同工况分析和任务事件；维修后以新证据独立复核。"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from agent_platform.core.errors import DomainError
from agent_platform.core.tasks import TaskService

from ..data.service import DataService
from ..data.sql_safety import SqlFieldValidationError, ValidationError
from . import models as m
from .schemas import decimal_text
from .service import ManufacturingService, view
from .workflow import as_utc, input_time, people_names, workspace_user


class MaintenanceWorkService(ManufacturingService):
    def order(self, order_id):
        row = super().get(m.MaintenanceWorkOrder, order_id)
        self.actor.require("m10.read")
        if row.incident_id:
            self.get(m.Incident, row.incident_id)
        if row.analysis_id:
            self.get(m.MaintenanceAnalysis, row.analysis_id)
        queries = {row.source_snapshot.get("query_id"), row.verification_snapshot.get("query_id")}
        events = self.session.scalars(select(m.MaintenanceWorkEvent).where(m.MaintenanceWorkEvent.order_id == row.id)).all()
        queries.update(event.evidence.get("query_id") for event in events)
        for query_id in queries - {None, ""}:
            try:
                DataService(self.session, self.actor).get_query(query_id)
            except ValidationError as exc:
                if isinstance(exc, SqlFieldValidationError) or exc.fields.get("table"):
                    raise DomainError(403, "maintenance_evidence_access_denied", "当前账号无法访问原始维修证据") from exc
                raise
        verification_id = row.verification_snapshot.get("analysis_id")
        if verification_id:
            self.get(m.MaintenanceAnalysis, verification_id)
        return row

    def order_view(self, row, *, events=False):
        device = self.get(m.Device, row.device_id, archived=True)
        names = people_names(self.session, self.actor, [row.assignee_id, row.performed_by, row.verified_by, row.created_by])
        result = {**view(row), "device_name": device.name, "assignee_name": names.get(row.assignee_id), "performed_by_name": names.get(row.performed_by), "verified_by_name": names.get(row.verified_by)}
        if events:
            history = self.session.scalars(select(m.MaintenanceWorkEvent).where(m.MaintenanceWorkEvent.order_id == row.id).order_by(m.MaintenanceWorkEvent.created_at, m.MaintenanceWorkEvent.id)).all()
            names.update(people_names(self.session, self.actor, [event.actor_id for event in history]))
            result["events"] = [{**view(event), "actor_name": names.get(event.actor_id)} for event in history]
        return result

    def orders(self, *, q="", status=None, device_id=None, limit=50, offset=0):
        clauses = [m.MaintenanceWorkOrder.workspace_id == self.actor.workspace_id, m.MaintenanceWorkOrder.archived.is_(False)]
        if status:
            clauses.append(m.MaintenanceWorkOrder.status == status)
        if device_id:
            clauses.append(m.MaintenanceWorkOrder.device_id == device_id)
        if q:
            clauses.append(m.MaintenanceWorkOrder.name.contains(q, autoescape=True))
        rows = self.session.scalars(select(m.MaintenanceWorkOrder).where(*clauses).order_by(m.MaintenanceWorkOrder.updated_at.desc(), m.MaintenanceWorkOrder.id))
        items, total = [], 0
        for row in rows:
            try:
                self.order(row.id)
            except DomainError as exc:
                if exc.status in (403, 404):
                    continue
                raise
            if max(offset, 0) <= total < max(offset, 0) + min(max(limit, 1), 200):
                items.append(self.order_view(row))
            total += 1
        return {"items": items, "total": total}

    def event(self, row, action, note, evidence=None):
        snapshot = {key: view(row)[key] for key in ("revision", "status", "assignee_id", "due_at", "root_cause", "repair_note", "repaired_at", "performed_by", "verified_by", "verified_at")}
        self.session.add(m.MaintenanceWorkEvent(order_id=row.id, action=action, note=note, evidence={**(evidence or {}), "state_snapshot": snapshot}, actor_id=self.actor.user_id))
        if row.incident_id:
            self.session.add(m.IncidentEvent(incident_id=row.incident_id, action=action, note=note, actor_id=self.actor.user_id))
        TaskService(self.session, self.actor).event(row.task_id, "maintenance_" + action, {"order_id": row.id})
        self.session.flush()

    def create_order(self, source_type, source_id, data):
        self.actor.require("m10.write")
        source = self.get(m.Incident if source_type == "incident" else m.MaintenanceAnalysis, source_id)
        if source.revision != data["revision"]:
            raise DomainError(409, "revision_conflict", "来源已更新，请刷新")
        field = m.MaintenanceWorkOrder.incident_id if source_type == "incident" else m.MaintenanceWorkOrder.analysis_id
        if self.session.scalar(select(m.MaintenanceWorkOrder.id).where(field == source.id, m.MaintenanceWorkOrder.workspace_id == self.actor.workspace_id)):
            raise DomainError(409, "work_order_exists", "该来源已有维修工单")
        if source_type == "incident":
            if source.status == "resolved":
                raise DomainError(409, "incident_resolved", "该预警已经结案")
            rule = self.get(m.AlertRule, source.rule_id)
            evidence = source.evidence.get("first", source.evidence)
            if not evidence.get("operator") or not evidence.get("rule_revision"):
                raise DomainError(409, "incident_standard_snapshot_missing", "原预警缺少标准快照，请重新执行预警")
            snapshot = {"type": "incident", "incident_id": source.id, "rule_id": rule.id, "rule_revision": evidence["rule_revision"],
                        "operator": evidence["operator"], "threshold": evidence["threshold"], "unit": evidence["unit"],
                        "query_id": evidence.get("provenance", {}).get("query_id"), "evidence": deepcopy(evidence)}
        else:
            if source.result.get("status") != "attention":
                raise DomainError(422, "maintenance_attention_required", "请选择有异常变化证据的维护分析")
            snapshot = {"type": "analysis", "analysis_id": source.id, "query_id": source.provenance.get("query_id"), "result": deepcopy(source.result)}
        self.change(type(source), source.id, {}, data["revision"])
        task = TaskService(self.session, self.actor).create("maintenance_work_order", data["name"], inputs={"source_type": source_type, "source_id": source.id, "device_id": source.device_id}, access_permission="m10.read")
        row = m.MaintenanceWorkOrder(workspace_id=self.actor.workspace_id, name=data["name"], device_id=source.device_id,
            incident_id=source.id if source_type == "incident" else None, analysis_id=source.id if source_type == "analysis" else None,
            source_snapshot=snapshot, task_id=task["id"], created_by=self.actor.user_id)
        self.session.add(row)
        self.session.flush()
        self.event(row, "created", data["note"], {"source_type": source_type, "source_id": source.id})
        self.audit("create", "maintenance_work_order", row.id)
        return self.order_view(row, events=True)

    def _verification(self, row, data):
        """只接受维修后的受控数据，不能由前端提交一个“已恢复”布尔值。"""
        if row.source_snapshot["type"] == "analysis":
            if not data.get("verification_analysis_id") or data.get("verification_query_id"):
                raise DomainError(422, "verification_analysis_required", "请选择维修后同设备的维护分析")
            analysis = self.get(m.MaintenanceAnalysis, data["verification_analysis_id"])
            if analysis.device_id != row.device_id or as_utc(analysis.created_at) < as_utc(row.repaired_at):
                raise DomainError(422, "verification_source_not_fresh", "复测分析须属于本设备且生成于维修后")
            if analysis.result.get("status") != "normal":
                raise DomainError(409, "verification_not_recovered", "复测数据尚未证明恢复稳定")
            if analysis.request_snapshot != self.get(m.MaintenanceAnalysis, row.analysis_id).request_snapshot:
                # 名称与时间参数可改变；比较口径字段必须相同，不能换指标规避异常。
                original = self.get(m.MaintenanceAnalysis, row.analysis_id).request_snapshot
                keys = ("value_field", "load_field", "period_field", "bin_width", "min_samples", "minimum_coverage", "change_threshold", "change_direction", "condition_fields")
                if any(analysis.request_snapshot.get(key) != original.get(key) for key in keys):
                    raise DomainError(422, "verification_criteria_changed", "复测须使用原来的指标和比较口径")
            return {"type": "analysis", "analysis_id": analysis.id, "query_id": analysis.provenance.get("query_id"), "result": deepcopy(analysis.result)}
        if not data.get("verification_query_id") or not data.get("value_field") or not data.get("time_field") or data.get("verification_analysis_id"):
            raise DomainError(422, "verification_query_required", "请选择复测查询及数值、时间字段")
        query = DataService(self.session, self.actor).get_query(data["verification_query_id"])
        original = row.source_snapshot.get("evidence", {}).get("provenance", {})
        if original.get("type") == "query" and (query.datasource_id != original.get("datasource_id") or data["value_field"] != original.get("value_field") or data["time_field"] != original.get("time_field")):
            raise DomainError(422, "verification_mapping_changed", "复测须使用原预警的数据源及指标映射")
        if as_utc(query.created_at) < as_utc(row.repaired_at) or query.result.get("truncated"):
            raise DomainError(422, "verification_source_not_fresh", "复测查询须在维修后执行且结果完整")
        required = {data["value_field"], data["time_field"]} | ({data["unit_field"]} if data.get("unit_field") else set())
        if not required.issubset(query.result["columns"]) or not query.result["rows"]:
            raise DomainError(422, "verification_mapping_invalid", "复测结果缺少所选字段或有效数据")
        observations = []
        for item in query.result["rows"]:
            stamp = input_time(str(item[data["time_field"]]))
            if stamp is None:
                raise DomainError(422, "verification_time_invalid", "复测观测时间不能为空")
            observations.append((stamp, item))
        stamp, observation = max(observations, key=lambda item: item[0])
        if stamp is None or stamp < as_utc(row.repaired_at):
            raise DomainError(422, "verification_observation_old", "复测观测时间必须在维修之后")
        if stamp > m.now() + timedelta(minutes=5):
            raise DomainError(422, "verification_observation_future", "复测观测时间不能晚于当前时间")
        unit = str(observation[data["unit_field"]]) if data.get("unit_field") else row.source_snapshot["unit"]
        if unit != row.source_snapshot["unit"]:
            raise DomainError(422, "unit_mismatch", "复测单位与原预警标准不一致")
        try:
            raw = decimal_text(str(observation[data["value_field"]]))
        except ValueError as exc:
            raise DomainError(422, "verification_value_invalid", "复测值不是有效的精确数值") from exc
        value, threshold = Decimal(raw), Decimal(row.source_snapshot["threshold"])
        matched = {"gt": value > threshold, "gte": value >= threshold, "lt": value < threshold, "lte": value <= threshold}[row.source_snapshot["operator"]]
        if matched:
            raise DomainError(409, "verification_not_recovered", "复测结果仍达到原预警条件")
        return {"type": "query", "query_id": query.id, "value": raw, "unit": unit, "observed_at": stamp.isoformat(), "operator": row.source_snapshot["operator"], "threshold": row.source_snapshot["threshold"]}

    def action(self, order_id, data):
        row = self.order(order_id)
        action = data["action"]
        self.actor.require("m10.publish" if action in ("verify", "reject") else "m10.write")
        if row.revision != data["revision"]:
            raise DomainError(409, "revision_conflict", "维修工单已更新，请刷新")
        changes, evidence = {}, {}
        tasks = TaskService(self.session, self.actor)
        if action == "comment":
            pass
        elif action == "assign" and row.status in ("open", "assigned", "in_progress"):
            if not data.get("assignee_id"):
                raise DomainError(422, "assignee_required", "请选择维修责任人")
            workspace_user(self.session, self.actor, data["assignee_id"], "m10.write")
            changes = {"status": "assigned", "assignee_id": data["assignee_id"], "due_at": input_time(data.get("due_at"))}
            if tasks.get(row.task_id)["status"] == "queued":
                tasks.transition(row.task_id, "running")
        elif action == "start" and row.status == "assigned":
            if row.assignee_id != self.actor.user_id:
                raise DomainError(403, "assignee_action_required", "该操作由维修责任人执行")
            changes = {"status": "in_progress"}
        elif action == "repair" and row.status == "in_progress":
            if row.assignee_id != self.actor.user_id:
                raise DomainError(403, "assignee_action_required", "该操作由维修责任人执行")
            if not data.get("root_cause"):
                raise DomainError(422, "root_cause_required", "请填写检查原因")
            if data.get("evidence_query_id"):
                query = DataService(self.session, self.actor).get_query(data["evidence_query_id"])
                evidence["query_id"] = query.id
            changes = {"status": "awaiting_review", "root_cause": data["root_cause"], "repair_note": data["note"],
                       "repaired_at": m.now(), "performed_by": self.actor.user_id, "verification_snapshot": {}}
            tasks.transition(row.task_id, "waiting_review")
        elif action in ("verify", "reject") and row.status == "awaiting_review":
            if self.actor.user_id == row.performed_by:
                raise DomainError(403, "independent_reviewer_required", "请选择另一名复核人完成复核")
            if action == "verify":
                evidence = self._verification(row, data)
                changes = {"status": "resolved", "verified_by": self.actor.user_id, "verified_at": m.now(), "verification_snapshot": evidence}
            else:
                changes = {"status": "in_progress"}
            tasks.transition(row.task_id, "succeeded" if action == "verify" else "running", outputs={"order_id": row.id, "verification": evidence})
        elif action == "reopen" and row.status == "resolved":
            task = tasks.create("maintenance_work_order", row.name, inputs={"order_id": row.id}, access_permission="m10.read")
            changes = {"status": "open", "task_id": task["id"], "assignee_id": None, "performed_by": None, "verified_by": None, "verified_at": None, "verification_snapshot": {},
                       "root_cause": "", "repair_note": "", "repaired_at": None, "due_at": None}
        else:
            raise DomainError(409, "invalid_transition", "当前状态不支持该操作")
        self.change(m.MaintenanceWorkOrder, row.id, changes, data["revision"])
        if row.incident_id and action not in ("comment",):
            incident = self.get(m.Incident, row.incident_id)
            self.change(m.Incident, incident.id, {"status": row.status}, incident.revision)
        self.event(row, action, data["note"], evidence)
        return self.order_view(row, events=True)
