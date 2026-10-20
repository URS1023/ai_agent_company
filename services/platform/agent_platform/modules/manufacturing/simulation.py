"""显式、限量的合成数据批次；按小步推进便于停用和持久心跳。"""
from datetime import timedelta, timezone
from io import StringIO
import csv
from time import monotonic

from sqlalchemy import select

from agent_platform.core.errors import DomainError
from agent_platform.core.tasks import TaskService
from ..data.service import DataService
from . import models as m
from .service import ManufacturingService
from .simulation_generator import VERSION, asset_definitions, generate, initial_state, parse_time

GENERATOR_ID = "food-equipment-v1"


class SimulationService:
    def __init__(self, session, actor, settings):
        self.session, self.actor, self.settings = session, actor, settings
        self.domain = ManufacturingService(session, actor)

    @staticmethod
    def status(row):
        config = row.parameters.get("config", {})
        return {"id": row.id, "revision": row.revision, "generator_id": row.generator_id, "name": row.name,
                "status": row.status, "source_kind": "synthetic", "sample_count": row.sample_count,
                "completed_steps": row.parameters.get("completed_steps", 0), "total_steps": config.get("steps", 0),
                "compute_seconds": row.parameters.get("compute_seconds", 0), "compute_budget_seconds": config.get("compute_budget_seconds"),
                "heartbeat_at": (row.heartbeat_at.replace(tzinfo=timezone.utc) if row.heartbeat_at.tzinfo is None else row.heartbeat_at).isoformat(), "dataset_id": row.dataset_id, "device_mapping": row.device_mapping,
                "execution_task_id": row.parameters.get("execution_task_id"), "error": row.parameters.get("error"),
                "advance_url": f"/api/v1/data-generator-activations/{row.id}/advances"}

    def create(self, config):
        try:
            parse_time(config.start_at)
        except (ValueError, TypeError) as exc:
            raise DomainError(422, "simulation_time_invalid", "合成开始时间须包含时区") from exc
        task = TaskService(self.session, self.actor).create("synthetic_data", config.name, inputs={"generator_id": GENERATOR_ID, "steps": config.steps, "source_kind": "synthetic"}, access_permission="m10.read")
        created = self.domain.create(m.SimulationActivation, {"generator_id": GENERATOR_ID, "name": config.name,
            "status": "queued", "parameters": {"config": config.model_dump(), "completed_steps": 0,
                "compute_seconds": 0, "state": initial_state(), "rows": [], "execution_task_id": task["id"]}})
        return self.status(self.domain.get(m.SimulationActivation, created["id"]))

    def get(self, activation_id):
        return self.status(self.domain.get(m.SimulationActivation, activation_id))

    def latest(self):
        row = self.session.scalar(select(m.SimulationActivation).where(m.SimulationActivation.workspace_id == self.actor.workspace_id, m.SimulationActivation.generator_id == GENERATOR_ID, m.SimulationActivation.archived.is_(False)).order_by(m.SimulationActivation.created_at.desc()).limit(1))
        return self.status(row) if row else {"id": GENERATOR_ID, "status": "stopped", "running": False, "source_kind": "synthetic"}

    def advance(self, activation_id, config):
        from copy import deepcopy
        row = self.domain.get(m.SimulationActivation, activation_id)
        if row.revision != config.revision:
            raise DomainError(409, "revision_conflict", "生成批次已更新，请刷新后重试")
        if row.status not in ("queued", "running"):
            raise DomainError(409, "simulation_not_running", "该合成批次已停止或结束")
        parameters = deepcopy(row.parameters)
        definition = parameters["config"]
        tasks = TaskService(self.session, self.actor)
        if row.status == "queued":
            tasks.transition(parameters["execution_task_id"], "running")
        started = monotonic()
        for _ in range(min(config.steps, definition["steps"] - parameters["completed_steps"])):
            at = parse_time(definition["start_at"]) + timedelta(seconds=definition["interval_seconds"] * parameters["completed_steps"])
            rows, _, _ = generate(at, definition["interval_seconds"], parameters["state"])
            parameters["rows"].extend(rows)
            parameters["completed_steps"] += 1
            if parameters["compute_seconds"] + monotonic() - started > definition["compute_budget_seconds"]:
                parameters.update(error="合成计算达到本批次总预算")
                self.domain.change(m.SimulationActivation, row.id, {"status": "failed", "parameters": parameters, "heartbeat_at": m.now()}, config.revision)
                tasks.transition(parameters["execution_task_id"], "failed", error={"code": "simulation_budget_exceeded"})
                return self.status(row)
        parameters["compute_seconds"] += monotonic() - started
        changes = {"parameters": parameters, "status": "running", "sample_count": len(parameters["rows"]), "heartbeat_at": m.now()}
        if parameters["completed_steps"] == definition["steps"]:
            output, columns = StringIO(newline=""), list(parameters["rows"][0])
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            writer.writerows(parameters["rows"])
            dataset = DataService(self.session, self.actor, self.settings).import_csv({"name": row.name, "csv_text": output.getvalue(), "source_kind": "synthetic", "description": "显式合成样本；算法来源Test5 " + VERSION})
            mapping = {}
            for asset in asset_definitions():
                code = "SIM-" + asset["device_id"]
                device = self.session.scalar(select(m.Device).where(m.Device.workspace_id == self.actor.workspace_id, m.Device.code == code, m.Device.archived.is_(False)))
                if device is None:
                    saved = self.domain.create(m.Device, {"name": "[模拟]" + asset["name"], "code": code, "device_type": asset["kind"], "description": "synthetic / " + VERSION})
                    mapping[asset["device_id"]] = saved["id"]
                else:
                    mapping[asset["device_id"]] = device.id
            parameters.pop("rows", None)
            parameters.pop("state", None)
            changes.update(status="succeeded", dataset_id=dataset["id"], device_mapping=mapping, parameters=parameters)
            tasks.transition(parameters["execution_task_id"], "succeeded", outputs={"dataset_id": dataset["id"], "sample_count": changes["sample_count"], "source_kind": "synthetic"})
        self.domain.change(m.SimulationActivation, row.id, changes, config.revision)
        return self.status(row)

    def cancel(self, activation_id, revision):
        row = self.domain.get(m.SimulationActivation, activation_id)
        if row.status not in ("queued", "running"):
            raise DomainError(409, "simulation_not_running", "生成批次已结束")
        tasks = TaskService(self.session, self.actor)
        task_id = row.parameters["execution_task_id"]
        task = tasks.get(task_id)
        # 平台同步小步执行不接收中途线程中断；停止在下一步派发前生效。
        if task["status"] == "running":
            tasks.transition(task_id, "cancel_requested")
        tasks.transition(task_id, "cancelled")
        self.domain.change(m.SimulationActivation, row.id, {"status": "cancelled", "heartbeat_at": m.now()}, revision)
        return self.status(row)
