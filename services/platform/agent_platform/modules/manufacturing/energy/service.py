"""节能分析只从已授权查询重跑一次，保存固定结果；历史读与导出重查当前字段权限。"""
from hashlib import sha256
import json

from sqlalchemy import select

from agent_platform.core.errors import DomainError

from ...data.service import DataService
from ...data.sql_safety import SqlFieldValidationError, ValidationError
from .. import models as m
from ..service import ManufacturingService, csv_content, view
from .analysis import analyze_energy
from .models import EnergyAnalysis


def evidence_hash(rows):
    return sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class EnergyService(ManufacturingService):
    def __init__(self, session, actor, settings=None):
        super().__init__(session, actor)
        self.settings = settings

    def energy(self, row_id):
        self.actor.require("m10.read")
        row = super().get(EnergyAnalysis, row_id)
        try:
            query = DataService(self.session, self.actor, self.settings).get_query(row.query_id)
        except ValidationError as exc:
            if isinstance(exc, SqlFieldValidationError) or exc.fields.get("table"):
                raise DomainError(403, "energy_evidence_access_denied", "当前账号无法访问原始能耗证据") from exc
            raise
        if row.provenance.get("query_id") != query.id or row.provenance.get("rows_sha256") != evidence_hash(query.result["rows"]):
            raise DomainError(422, "energy_evidence_invalid", "能耗结果与固定来源证据不一致")
        return row

    def energy_view(self, row):
        device = self.get(m.Device, row.device_id, archived=True)
        return {**view(row), "device_name": device.name}

    def listing(self, *, q="", device_id=None, limit=50, offset=0):
        self.actor.require("m10.read")
        clauses = [EnergyAnalysis.workspace_id == self.actor.workspace_id, EnergyAnalysis.archived.is_(False)]
        if q:
            clauses.append(EnergyAnalysis.name.contains(q, autoescape=True))
        if device_id:
            clauses.append(EnergyAnalysis.device_id == device_id)
        items, count = [], 0
        for row in self.session.scalars(select(EnergyAnalysis).where(*clauses).order_by(EnergyAnalysis.created_at.desc(), EnergyAnalysis.id)):
            try:
                self.energy(row.id)
            except DomainError as exc:
                if exc.status in (403, 404):
                    continue
                raise
            if max(offset, 0) <= count < max(offset, 0) + min(max(limit, 1), 200):
                items.append(self.energy_view(row))
            count += 1
        return {"items": items, "total": count}

    def calculate(self, config):
        self.actor.require("m10.write")
        self.actor.require("m06.read")
        self.get(m.Device, config.device_id)
        data = DataService(self.session, self.actor, self.settings)
        saved = data.get_query(config.query_id)
        fields = {config.time_field, config.period_field, config.energy_field, config.production_field, config.load_field, *config.condition_fields}
        fields.update(value for value in (config.energy_unit_field, config.production_unit_field, config.duration_field, config.price_field, config.device_field) if value)
        if not fields.issubset(saved.result["columns"]):
            raise DomainError(422, "energy_mapping_missing", "查询结果缺少所选字段", {"fields": sorted(fields - set(saved.result["columns"]))})
        inputs = {"device_id": config.device_id, "query_id": saved.id, "energy_mode": config.energy_mode}
        tasks, task_id = self._execution("energy_analysis", config.name, inputs, "m10.read")
        try:
            query = data.execute_query({"datasource_id": saved.datasource_id, "sql": saved.sql, "parameters": saved.parameters, "max_rows": 10000})
            if query["truncated"]:
                raise DomainError(422, "energy_source_truncated", "能耗来源结果不完整，请缩小查询范围")
            if not fields.issubset(query["columns"]):
                raise DomainError(422, "energy_mapping_missing", "当前来源缺少所选字段", {"fields": sorted(fields - set(query["columns"]))})
            result = analyze_energy(query["rows"], config)
            row = EnergyAnalysis(workspace_id=self.actor.workspace_id, name=config.name, device_id=config.device_id, query_id=query["id"],
                request_snapshot=config.model_dump(), result=result,
                provenance={"type": "query", "query_id": query["id"], "source_query_id": saved.id, "source_query_name": saved.name, "datasource_id": saved.datasource_id,
                            "row_count": len(query["rows"]), "rows_sha256": evidence_hash(query["rows"]),
                            "executed_sql": query["executed_sql"], "parameters": saved.parameters, "timezone": config.timezone,
                            "device_scope": {"field": config.device_field, "value": config.device_value, "mode": "mapped_device" if config.device_field else "selected_query"}},
                task_id=task_id, created_by=self.actor.user_id)
            self.session.add(row)
            self.session.flush()
            tasks.transition(task_id, "succeeded", outputs={"energy_analysis_id": row.id, "business_status": result["status"], "result_url": f"/api/v1/energy-analyses/{row.id}"})
            self.audit("calculate", "energy_analysis", row.id)
            return self.energy_view(row)
        except Exception as exc:
            self._failed_execution(exc, "energy_analysis", config.name, inputs, "m10.read")
            raise

    def rename(self, row_id, data):
        self.actor.require("m10.write")
        row = self.energy(row_id)
        self.change(EnergyAnalysis, row.id, {"name": data["name"]}, data["revision"])
        return self.energy_view(row)

    def remove(self, row_id, revision):
        self.actor.require("m10.delete")
        row = self.energy(row_id)
        return self.change(EnergyAnalysis, row.id, {"archived": True}, revision)

    def export(self, row_id):
        row = self.energy(row_id)
        records = [{**group, "analysis_name": row.name, "energy_unit": "kWh", "production_unit": row.result["production_unit"],
                    "coverage": row.result["coverage"], "source_query_id": row.query_id, "method": row.result["method"]} for group in row.result["bins"]]
        self.audit("export", "energy_analysis", row.id)
        return csv_content(records)
