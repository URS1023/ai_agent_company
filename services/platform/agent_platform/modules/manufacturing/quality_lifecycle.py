"""复用既有检验/标准/样本入口，追加复检血缘和不可覆盖的质量处置历史。"""
from copy import deepcopy

from sqlalchemy import select

from agent_platform.core.errors import DomainError
from agent_platform.core.tasks import TaskService

from . import models as m
from .service import ManufacturingService, view
from .workflow import authorize_run, input_time, people_names, workspace_user


class QualityService(ManufacturingService):
    def inspection(self, inspection_id):
        row = self.session.scalar(select(m.Inspection).where(m.Inspection.id == inspection_id, m.Inspection.workspace_id == self.actor.workspace_id, m.Inspection.archived.is_(False)))
        if row is None:
            raise DomainError(404, "resource_not_found", "检验任务不存在")
        return row

    def case(self, case_id):
        row = super().get(m.QualityCase, case_id)
        authorize_run(self.session, self.actor, self.inspection(row.inspection_id), row.source_run_id)
        for event in self.session.scalars(select(m.QualityCaseEvent).where(m.QualityCaseEvent.case_id == row.id)):
            if event.evidence.get("run_id"):
                authorize_run(self.session, self.actor, self.inspection(event.evidence.get("inspection_id") or row.inspection_id), event.evidence["run_id"])
        return row

    def case_view(self, row, *, events=False):
        inspection = self.inspection(row.inspection_id)
        names = people_names(self.session, self.actor, [row.assignee_id, row.performed_by, row.closed_by, row.created_by])
        result = {**view(row), "inspection_name": inspection.name, "target_type": inspection.target_type, "assignee_name": names.get(row.assignee_id), "performed_by_name": names.get(row.performed_by), "closed_by_name": names.get(row.closed_by)}
        if row.reinspection_id:
            child = self.inspection(row.reinspection_id)
            self._authorize_evidence(child)
            result["reinspection"] = {"id": child.id, "name": child.name, "status": child.status, "outcome": child.outcome, "revision": child.revision}
        if events:
            history = self.session.scalars(select(m.QualityCaseEvent).where(m.QualityCaseEvent.case_id == row.id).order_by(m.QualityCaseEvent.created_at, m.QualityCaseEvent.id)).all()
            people = people_names(self.session, self.actor, [event.actor_id for event in history])
            result["events"] = [{**view(event), "actor_name": people.get(event.actor_id)} for event in history]
        return result

    def cases(self, *, inspection_id=None, target_type=None, status=None, limit=50, offset=0):
        clauses = [m.QualityCase.workspace_id == self.actor.workspace_id, m.QualityCase.archived.is_(False)]
        if inspection_id:
            clauses.append(m.QualityCase.inspection_id == inspection_id)
        if status:
            clauses.append(m.QualityCase.status == status)
        rows = self.session.scalars(select(m.QualityCase).where(*clauses).order_by(m.QualityCase.updated_at.desc(), m.QualityCase.id))
        items, total = [], 0
        for row in rows:
            try:
                self.case(row.id)
                if target_type and self.inspection(row.inspection_id).target_type != target_type:
                    continue
            except DomainError as exc:
                if exc.status in (403, 404):
                    continue
                raise
            if max(offset, 0) <= total < max(offset, 0) + min(max(limit, 1), 200):
                items.append(self.case_view(row))
            total += 1
        return {"items": items, "total": total}

    def event(self, case, action, note, evidence=None):
        snapshot = {key: view(case)[key] for key in ("revision", "status", "disposition", "assignee_id", "due_at", "root_cause", "corrective_action", "performed_by", "reinspection_id", "closed_by", "closed_at")}
        self.session.add(m.QualityCaseEvent(case_id=case.id, action=action, note=note, evidence={**(evidence or {}), "state_snapshot": snapshot}, actor_id=self.actor.user_id))
        if case.task_id:
            TaskService(self.session, self.actor).event(case.task_id, "quality_case_" + action, {"case_id": case.id})
        self.session.flush()

    def create_case(self, data):
        self.actor.require("m11.write")
        inspection = self.inspection(data["inspection_id"])
        run = authorize_run(self.session, self.actor, inspection, data["run_id"])
        if inspection.revision != data["revision"]:
            raise DomainError(409, "revision_conflict", "检验任务已更新，请刷新")
        if inspection.status != "reviewed" or inspection.result.get("run_id") != run.id or run.result.get("outcome") == "passed":
            raise DomainError(409, "quality_source_not_actionable", "请选择已复核且需处置的当前检验运行")
        if self.session.scalar(select(m.QualityCase.id).where(m.QualityCase.workspace_id == self.actor.workspace_id, m.QualityCase.source_run_id == run.id)):
            raise DomainError(409, "quality_case_exists", "该次检验已有处置单")
        self.change(m.Inspection, inspection.id, {}, data["revision"])
        task = TaskService(self.session, self.actor).create("quality_case", data["name"], inputs={"inspection_id": inspection.id, "run_id": run.id}, access_permission="m11.read")
        row = m.QualityCase(workspace_id=self.actor.workspace_id, name=data["name"], inspection_id=inspection.id, source_run_id=run.id, task_id=task["id"], created_by=self.actor.user_id)
        self.session.add(row)
        self.session.flush()
        self.event(row, "created", data["note"], {"run_id": run.id, "system_outcome": run.result["outcome"]})
        self.audit("create", "quality_case", row.id)
        return self.case_view(row, events=True)

    def create_reinspection(self, inspection_id, data):
        self.actor.require("m11.write")
        parent = self.inspection(inspection_id)
        run = authorize_run(self.session, self.actor, parent, data["run_id"])
        if parent.revision != data["revision"]:
            raise DomainError(409, "revision_conflict", "检验任务已更新，请刷新")
        if parent.status != "reviewed" or parent.result.get("run_id") != run.id:
            raise DomainError(409, "reinspection_source_not_reviewed", "请先复核当前检验运行")
        case = self.case(data["case_id"]) if data.get("case_id") else None
        if case:
            previous = self.inspection(case.reinspection_id) if case.reinspection_id else None
            if case.inspection_id != parent.id or case.source_run_id != run.id or case.status != "awaiting_reinspection" or (previous and (previous.status != "reviewed" or previous.outcome == "passed")):
                raise DomainError(409, "case_reinspection_not_allowed", "当前处置单不能关联这次复检")
        samples = run.result.get("samples", [])
        if data.get("sample_ids") is not None:
            selected = data["sample_ids"]
            if len(set(selected)) != len(selected) or not set(selected).issubset({sample["sample_id"] for sample in samples}):
                raise DomainError(422, "reinspection_samples_invalid", "复检样本须来自指定检验运行且不得重复")
            chosen = [sample for sample in samples if sample["sample_id"] in selected]
            expected = len(chosen)
        else:
            chosen = [sample for sample in samples if sample.get("outcome") != "passed"]
            missing = run.result.get("missing_samples", 0)
            if not chosen and not missing:
                chosen = samples
            expected = len(chosen) + missing
        if not expected:
            raise DomainError(422, "reinspection_samples_missing", "没有可复检的样本")
        previous_link = self.session.scalar(select(m.InspectionReinspection).where(m.InspectionReinspection.child_inspection_id == parent.id, m.InspectionReinspection.workspace_id == self.actor.workspace_id))
        self.change(m.Inspection, parent.id, {}, data["revision"])
        # 不调用可变主标准创建接口；复检严格复用父任务已经固定的标准和对象。
        child = m.Inspection(workspace_id=self.actor.workspace_id, name=data["name"], target_type=parent.target_type,
                             template_id=parent.template_id, template_revision=parent.template_revision,
                             standard_snapshot=deepcopy(parent.standard_snapshot), device_id=parent.device_id,
                             product_id=parent.product_id, batch_id=parent.batch_id, expected_samples=expected)
        self.session.add(child)
        self.session.flush()
        references = [sample.get("external_ref") or f"样本 {index + 1}" for index, sample in enumerate(chosen)]
        self.session.add(m.InspectionReinspection(workspace_id=self.actor.workspace_id, parent_inspection_id=parent.id,
            source_run_id=run.id, child_inspection_id=child.id, root_inspection_id=previous_link.root_inspection_id if previous_link else parent.id,
            selected_sample_refs=references, reason=data["reason"], created_by=self.actor.user_id))
        for reference in references:
            self.session.add(m.InspectionSample(workspace_id=self.actor.workspace_id, inspection_id=child.id, external_ref=reference, metadata_json={"source_run_id": run.id}))
        if case:
            self.change(m.QualityCase, case.id, {"reinspection_id": child.id}, case.revision)
            self.event(case, "reinspection_created", data["reason"], {"inspection_id": child.id, "source_run_id": run.id})
        TaskService(self.session, self.actor).event(run.task_id, "inspection_reinspection_created", {"inspection_id": child.id})
        self.session.flush()
        self.audit("reinspect", "inspection", parent.id, {"child_inspection_id": child.id})
        return {**self.inspection_views([child])[0], "parent_inspection_id": parent.id, "source_run_id": run.id}

    def lineage(self, inspection_id):
        self.actor.require("m11.read")
        self.inspection(inspection_id)
        own = self.session.scalar(select(m.InspectionReinspection).where(m.InspectionReinspection.child_inspection_id == inspection_id, m.InspectionReinspection.workspace_id == self.actor.workspace_id))
        root = own.root_inspection_id if own else inspection_id
        links = self.session.scalars(select(m.InspectionReinspection).where(m.InspectionReinspection.workspace_id == self.actor.workspace_id, m.InspectionReinspection.root_inspection_id == root).order_by(m.InspectionReinspection.created_at, m.InspectionReinspection.id)).all()
        ids = {root, *(link.child_inspection_id for link in links)}
        rows = [self.inspection(row_id) for row_id in ids]
        for link in links:
            authorize_run(self.session, self.actor, self.inspection(link.parent_inspection_id), link.source_run_id)
        for row in rows:
            self._authorize_evidence(row)
        return {"root_inspection_id": root, "items": self.inspection_views(sorted(rows, key=lambda item: item.created_at)), "links": [view(link) for link in links], "total": len(rows)}

    def _reinspection_ready(self, case):
        if not case.reinspection_id:
            raise DomainError(409, "reinspection_required", "请先完成关联复检")
        child = self.inspection(case.reinspection_id)
        self._authorize_evidence(child)
        if child.status != "reviewed" or child.outcome != "passed" or child.result.get("outcome") != "passed" or child.result.get("missing_samples"):
            raise DomainError(409, "reinspection_not_passed", "关联复检尚未完整合格并复核")
        return {"inspection_id": child.id, "run_id": child.result["run_id"], "revision": child.revision,
                "system_outcome": child.result["outcome"], "review_outcome": child.outcome, "standard_revision": child.template_revision}

    def action(self, case_id, data):
        row = self.case(case_id)
        action = data["action"]
        self.actor.require("m11.publish" if action in ("verify", "reject") else "m11.write")
        if row.revision != data["revision"]:
            raise DomainError(409, "revision_conflict", "处置单已更新，请刷新")
        changes, evidence = {}, {}
        tasks = TaskService(self.session, self.actor)
        if action == "comment":
            pass
        elif action == "contain" and row.status == "open":
            changes = {"status": "contained"}
        elif action == "assign" and row.status in ("open", "contained", "assigned", "in_progress"):
            if not data.get("assignee_id"):
                raise DomainError(422, "assignee_required", "请选择责任人")
            workspace_user(self.session, self.actor, data["assignee_id"], "m11.write")
            changes = {"status": "assigned", "assignee_id": data["assignee_id"], "due_at": input_time(data.get("due_at"))}
            if tasks.get(row.task_id)["status"] == "queued":
                tasks.transition(row.task_id, "running")
        elif action == "start" and row.status == "assigned":
            if self.actor.user_id != row.assignee_id:
                raise DomainError(403, "assignee_action_required", "该操作由责任人执行")
            changes = {"status": "in_progress"}
        elif action == "submit" and row.status == "in_progress":
            if self.actor.user_id != row.assignee_id:
                raise DomainError(403, "assignee_action_required", "该操作由责任人执行")
            if data.get("disposition") in (None, "hold") or not data.get("root_cause") or not data.get("corrective_action"):
                raise DomainError(422, "corrective_action_required", "请选择处置方式并填写原因与处理结果")
            needs_reinspection = data["disposition"] in ("repair", "rework")
            changes = {"status": "awaiting_reinspection" if needs_reinspection else "awaiting_review", "disposition": data["disposition"],
                       "root_cause": data["root_cause"], "corrective_action": data["corrective_action"], "performed_by": self.actor.user_id, "reinspection_id": None}
            if not needs_reinspection:
                tasks.transition(row.task_id, "waiting_review")
        elif action == "submit_reinspection" and row.status == "awaiting_reinspection":
            if self.actor.user_id != row.assignee_id:
                raise DomainError(403, "assignee_action_required", "该操作由责任人执行")
            evidence = self._reinspection_ready(row)
            changes = {"status": "awaiting_review"}
            tasks.transition(row.task_id, "waiting_review")
        elif action in ("verify", "reject") and row.status == "awaiting_review":
            if self.actor.user_id == row.performed_by:
                raise DomainError(403, "independent_reviewer_required", "请选择另一名复核人完成复核")
            if row.disposition in ("repair", "rework"):
                evidence = self._reinspection_ready(row)
            changes = {"status": "closed", "closed_by": self.actor.user_id, "closed_at": m.now()} if action == "verify" else {"status": "in_progress"}
            tasks.transition(row.task_id, "succeeded" if action == "verify" else "running", outputs={"case_id": row.id, "disposition": row.disposition, **evidence})
        elif action == "reopen" and row.status == "closed":
            task = tasks.create("quality_case", row.name, inputs={"case_id": row.id, "source_run_id": row.source_run_id}, access_permission="m11.read")
            changes = {"status": "open", "task_id": task["id"], "assignee_id": None, "performed_by": None, "closed_by": None, "closed_at": None,
                       "disposition": "hold", "root_cause": "", "corrective_action": "", "reinspection_id": None, "due_at": None}
        else:
            raise DomainError(409, "invalid_transition", "当前状态不支持该操作")
        self.change(m.QualityCase, row.id, changes, data["revision"])
        self.event(row, action, data["note"], evidence)
        return self.case_view(row, events=True)
