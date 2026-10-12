"""Create a small, coherent business example through Test5's public APIs.

The script is deliberately additive and idempotent.  It never writes directly
to the platform database, never changes pre-existing user records, and only
reuses records carrying the stable DEMO codes/names defined below.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8011"
VEHICLE_SQL = (
    "SELECT autoId, c_chepaihaoma, c_cheliangpinpai, c_cheliangxinghao, "
    "c_dangqianlicheng, c_shifoudadaobaoyanglicheng, c_xiacibaoyanglicheng "
    "FROM t_cheliangxinxi1 WHERE isDeleted = 0 ORDER BY autoId LIMIT 12"
)


def _dotenv_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value
    return ""


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail", payload.get("message", ""))
        if isinstance(detail, dict):
            return str(detail.get("message") or detail.get("detail") or "请求未完成")
        if isinstance(detail, list):
            messages = [str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail]
            return "; ".join(messages)
        if detail:
            return str(detail)
    return "请求未完成"


class ApiClient:
    """Minimal JSON client that keeps credentials out of output and exceptions."""

    def __init__(self, base_url: str, *, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = ""
        self.workspace_id = ""

    def request(self, method: str, path: str, body: Any = None, *, authenticated: bool = True) -> Any:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Authorization"] = "Bearer " + self.token
            headers["X-Workspace-Id"] = self.workspace_id
        request = Request(self.base_url + path, data=payload, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                document = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                document = json.loads(exc.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                document = {}
            raise RuntimeError(f"{method} {path} 返回 HTTP {exc.code}：{_error_message(document)}") from None
        except URLError as exc:
            raise RuntimeError(f"无法连接 {self.base_url}：{exc.reason}") from None
        if isinstance(document, dict) and document.get("success") is True and "data" in document:
            return document["data"]
        return document

    def login(self, username: str, password: str, workspace_id: str = "") -> dict:
        session = self.request(
            "POST", "/api/auth/login", {"username": username, "password": password}, authenticated=False,
        )
        workspaces = session.get("workspaces") or []
        selected = next((item for item in workspaces if item.get("id") == workspace_id), None) if workspace_id else (workspaces[0] if workspaces else None)
        if not session.get("token") or selected is None:
            raise RuntimeError("登录成功但没有可用工作区，或指定工作区不属于当前账号")
        self.token = session["token"]
        self.workspace_id = selected["id"]
        return selected

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body)


def alert_device_payload(datasource_id: str) -> dict:
    return {
        "code": "DEMO-FLEET-01",
        "name": "示例·车队保养监测",
        "category": "vehicle-fleet",
        "model": "OA-FLEET",
        "manufacturer": "企业车辆台账",
        "location": "行政车队",
        "protocol": "DATABASE",
        "datasourceId": datasource_id,
        "qualityDatasourceIds": [],
        "domains": ["alert", "quality"],
        "description": "通过 variantorm 的 OA 车辆信息表读取车牌、当前里程与保养到期标记。",
        "status": "active",
    }


def point_payloads(device_id: str) -> list[dict]:
    return [
        {"deviceId": device_id, "code": "ROW-ID", "name": "车辆记录号", "fieldName": "autoId", "unit": "", "pointType": "integer", "deviceKey": "", "prefix": "", "frequencyMinutes": 60, "failureDiagnosis": False, "description": "车辆信息表主键，用于数据追溯。", "status": "active"},
        {"deviceId": device_id, "code": "VEHICLE-NO", "name": "车牌号码", "fieldName": "c_chepaihaoma", "unit": "", "pointType": "string", "deviceKey": "", "prefix": "", "frequencyMinutes": 60, "failureDiagnosis": False, "description": "预警问题的业务定位字段。", "status": "active"},
        {"deviceId": device_id, "code": "MILEAGE", "name": "当前里程", "fieldName": "c_dangqianlicheng", "unit": "km", "pointType": "decimal", "deviceKey": "", "prefix": "", "frequencyMinutes": 60, "failureDiagnosis": True, "description": "用于判断车辆是否进入保养处置区间。", "status": "active"},
        {"deviceId": device_id, "code": "MAINT-DUE", "name": "达到保养里程", "fieldName": "c_shifoudadaobaoyanglicheng", "unit": "", "pointType": "integer", "deviceKey": "", "prefix": "", "frequencyMinutes": 60, "failureDiagnosis": True, "description": "来源系统的保养到期标记，1 表示已达到。", "status": "active"},
    ]


def alert_advice_payload() -> dict:
    return {
        "name": "示例·车辆到期保养处置预案",
        "conditionCode": "VEHICLE-MAINT-DUE",
        "operatingCondition": "车辆处于在用状态且里程持续增加",
        "alertType": "计划保养",
        "conditionDescription": "保养标记为 1，同时当前里程达到 6000 km 或以上。",
        "content": "先核对车牌、当前里程和下次保养里程，再联系车辆管理员确认保养计划。",
        "responsePlan": "1. 确认车辆位置；2. 核对保养记录；3. 安排进厂时间；4. 完成后关闭问题并填写凭证。",
        "planShort": "核对并安排保养",
        "status": "active",
    }


def alert_rule_payload(device_id: str, advice_id: str) -> dict:
    return {
        "name": "示例·车辆保养预警策略",
        "description": "演示数据库采集、字段提取、AND 组合比较、报告生成与问题闭环。",
        "deviceId": device_id,
        "adviceId": advice_id,
        "recordKeyField": "c_chepaihaoma",
        "rules": [{
            "id": "demo-maintenance-due",
            "name": "示例·保养到期且里程达标",
            "expression": {
                "op": "and",
                "args": [
                    {"op": "eq", "args": [{"field": "c_shifoudadaobaoyanglicheng"}, {"value": 1}]},
                    {"op": "gte", "args": [{"field": "c_dangqianlicheng"}, {"value": 6000}]},
                ],
            },
            "severity": "warning",
            "message": "车辆已达到计划保养条件，请核对里程并安排保养。",
        }],
        "status": "active",
    }


def alert_workflow_payload(source: dict, rule: dict, points: list[dict]) -> dict:
    return {
        "requestKey": "demo-fleet-alert-flow-v1",
        "name": "示例·车辆保养数据库预警流程",
        "description": "从 variantorm 读取真实车辆台账，提取测点并按固定策略生成报告和问题。",
        "ruleId": rule["id"],
        "ruleRevision": rule["version"],
        "source": {
            "datasourceId": source["id"],
            "datasourceVersion": source["version"],
            "query": VEHICLE_SQL,
            "parameters": {},
            "maxRows": 100,
        },
        "pointIds": [item["id"] for item in points],
        "calculations": [],
        "notification": None,
        "writeback": None,
        "schedule": None,
    }


def quality_metric_payloads() -> list[dict]:
    return [
        {"name": "示例·碳含量（C）", "category": "component", "unit": "%", "standardMin": "0.18", "standardMax": "0.24", "componentName": "C", "minimumConfidence": "0.8", "blockingSeverities": ["moderate", "severe"], "description": "来料光谱成分检测的碳含量允许范围，包含上下限。", "status": "active"},
        {"name": "示例·表面划痕", "category": "appearance", "unit": "", "standardMin": None, "standardMax": None, "componentName": "", "minimumConfidence": "0.85", "blockingSeverities": ["moderate", "severe"], "description": "外观视觉检测；中度或严重划痕直接判为不合格。", "status": "active"},
    ]


def quality_device_payload() -> dict:
    return {
        "code": "DEMO-QC-01",
        "name": "示例·来料质检工作站",
        "category": "inspection-station",
        "model": "QC-MANUAL-01",
        "manufacturer": "企业质量中心",
        "location": "质量实验室",
        "protocol": "MANUAL",
        "datasourceId": None,
        "qualityDatasourceIds": [],
        "domains": ["quality"],
        "description": "演示成分检测、外观缺陷、逐项判定、人工复核与报告追溯。",
        "status": "active",
    }


def quality_template_payload(metric_ids: list[str]) -> dict:
    return {
        "name": "示例·来料双项检验标准",
        "deviceType": "inspection-station",
        "description": "同时检查碳含量和表面划痕；模板修订会固定当时的两项标准。",
        "metricIds": metric_ids,
        "status": "active",
    }


def inspection_payloads(template: dict, device: dict, metrics: list[dict]) -> list[dict]:
    component = next(item for item in metrics if item["category"] == "component")
    appearance = next(item for item in metrics if item["category"] == "appearance")
    return [
        {
            "templateId": template["id"], "templateRevision": template["version"],
            "deviceId": device["id"], "sampleId": "DEMO-QC-PASS-001",
            "requestKey": "demo-quality-pass-001",
            "observations": [
                {"metricId": component["id"], "value": "0.21", "unit": "%", "note": "光谱仪人工录入示例值。"},
                {"metricId": appearance["id"], "imageRef": "manual://demo/pass", "defects": [], "note": "外观检测未发现缺陷。"},
            ],
            "detectionIds": [],
        },
        {
            "templateId": template["id"], "templateRevision": template["version"],
            "deviceId": device["id"], "sampleId": "DEMO-QC-FAIL-001",
            "requestKey": "demo-quality-fail-001",
            "observations": [
                {"metricId": component["id"], "value": "0.27", "unit": "%", "note": "碳含量高于标准上限。"},
                {"metricId": appearance["id"], "imageRef": "manual://demo/fail", "defects": [{"defectType": "纵向划痕", "confidence": "0.96", "severity": "severe", "bbox": [0.18, 0.22, 0.34, 0.16], "suggest": "隔离样品并复检同批次来料。"}], "note": "外观检测发现严重划痕。"},
            ],
            "detectionIds": [],
        },
    ]


def application_payload(workflow_id: str, datasource_id: str) -> dict:
    return {
        "code": "demo-equipment-quality-solution",
        "name": "示例·设备预警与质量协同方案",
        "description": "从真实数据采集和预警工作流进入问题闭环，并关联质量检查能力。",
        "kind": "solution",
        "visibility": "workspace",
        "spec": {
            "welcomeMessage": "从车辆保养预警开始，可在设备与预警、质量检查和运行记录中查看完整证据链。",
            "instructions": "运行入口会读取 variantorm 车辆台账并生成预警报告。质量检查使用独立的指标与模板示例，所有结果均可追溯。",
            "bindings": [
                {"type": "workflow", "id": workflow_id, "role": "entry", "purpose": "真实车辆保养预警入口", "required": True},
                {"type": "datasource", "id": datasource_id, "role": "dependency", "purpose": "车辆台账只读数据来源", "required": True},
            ],
            "audience": {"mode": "workspace", "roleIds": [], "userIds": []},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": {"type": "object", "properties": {}},
        },
    }


def _unique(records: list[dict], key: str, value: str, label: str) -> dict | None:
    found = [item for item in records if item.get(key) == value]
    if len(found) > 1:
        raise RuntimeError(f"发现多个同名 {label}，请先保留一个：{value}")
    return found[0] if found else None


def _query(**values: Any) -> str:
    return urlencode({key: value for key, value in values.items() if value not in (None, "")})


def _record(client: ApiClient, path: str, *, key: str, value: str, payload: dict, label: str) -> tuple[dict, bool]:
    # Some registries search names only while the stable key is a code.  Read
    # the newest authorized page and perform an exact comparison client-side.
    records = client.get(path + "?" + _query(limit=100))
    existing = _unique(records, key, value, label)
    return (existing, False) if existing else (client.post(path, payload), True)


def seed(client: ApiClient, source_name: str) -> dict:
    sources = [item for item in client.get("/api/v1/analytics/datasources?enabled_only=true") if item.get("name") == source_name]
    if len(sources) != 1:
        raise RuntimeError(f"需要且只能有一个启用中的 {source_name!r} 数据源，当前找到 {len(sources)} 个")
    source = sources[0]
    if source.get("metadataStatus") != "ready":
        raise RuntimeError(f"数据源 {source_name!r} 元数据尚未就绪，请先在数据源页面完成同步")

    created: list[str] = []
    alert_device, made = _record(client, "/api/v1/alerts/devices", key="code", value="DEMO-FLEET-01", payload=alert_device_payload(source["id"]), label="预警设备")
    if made:
        created.append("预警设备")

    point_rows = client.get("/api/v1/alerts/points?" + _query(limit=200, deviceId=alert_device["id"]))
    points: list[dict] = []
    for payload in point_payloads(alert_device["id"]):
        existing = _unique(point_rows, "code", payload["code"], "设备测点")
        point = existing or client.post("/api/v1/alerts/points", payload)
        points.append(point)
        if existing is None:
            point_rows.append(point)
            created.append("设备测点")

    advice, made = _record(client, "/api/v1/alerts/advice", key="name", value="示例·车辆到期保养处置预案", payload=alert_advice_payload(), label="专家经验")
    if made:
        created.append("专家经验")
    rule, made = _record(client, "/api/v1/alerts/rules", key="name", value="示例·车辆保养预警策略", payload=alert_rule_payload(alert_device["id"], advice["id"]), label="预警策略")
    if made:
        created.append("预警策略")

    flow_result = client.post("/api/v1/alerts/workflows", alert_workflow_payload(source, rule, points))
    workflow = flow_result["workflow"]
    if not flow_result.get("reused"):
        created.append("预警工作流")
    execution = client.post(f"/api/v1/workflows/{workflow['id']}/runs", {
        "version": workflow["version"], "mode": "published",
        "idempotencyKey": "demo-fleet-alert-run-v1", "inputs": {},
        "confirmWrites": False, "timeoutSeconds": 120,
    })
    report_id = execution.get("result", {}).get("outputs", {}).get("end", {}).get("alertReportId")
    if not report_id:
        raise RuntimeError("示例工作流已执行，但没有返回预警报告标识")
    report = client.get(f"/api/v1/alerts/reports/{report_id}")
    problems = client.get("/api/v1/alerts/problems?" + _query(limit=200, reportId=report_id))
    if problems:
        first = problems[0]
        history = client.get(f"/api/v1/alerts/problems/{first['id']}/actions?limit=50")
        if not history:
            first = client.post(f"/api/v1/alerts/problems/{first['id']}/actions", {
                "version": first["version"], "status": "acknowledged",
                "note": "示例处置：已分配车辆管理员核对保养计划，等待确认进厂时间。",
            })
            created.append("问题处置记录")
            problems[0] = first

    quality_device, made = _record(client, "/api/v1/alerts/devices", key="code", value="DEMO-QC-01", payload=quality_device_payload(), label="质检设备")
    if made:
        created.append("质检设备")
    metrics: list[dict] = []
    for payload in quality_metric_payloads():
        metric, made = _record(client, "/api/v1/quality/metrics", key="name", value=payload["name"], payload=payload, label="质检指标")
        metrics.append(metric)
        if made:
            created.append("质检指标")
    template, made = _record(client, "/api/v1/quality/templates", key="name", value="示例·来料双项检验标准", payload=quality_template_payload([item["id"] for item in metrics]), label="质检模板")
    if made:
        created.append("质检模板")

    inspections = [client.post("/api/v1/quality/inspections", payload) for payload in inspection_payloads(template, quality_device, metrics)]
    failed = next(item for item in inspections if item["sampleId"] == "DEMO-QC-FAIL-001")
    appearance = next(item for item in metrics if item["category"] == "appearance")
    if not failed.get("reviews"):
        failed = client.post(f"/api/v1/quality/inspections/{failed['id']}/reviews", {
            "version": failed["version"], "metricId": appearance["id"], "decision": "failed",
            "note": "示例复核：缺陷位置与置信度证据完整，确认该样品外观不合格并隔离同批次。",
        })
        inspections = [failed if item["id"] == failed["id"] else item for item in inspections]
        created.append("人工复核记录")

    applications = client.get("/api/v1/applications?" + _query(limit=200, keyword="demo-equipment-quality-solution"))
    application = _unique(applications.get("items", []), "code", "demo-equipment-quality-solution", "应用")
    if application is None:
        application = client.post("/api/v1/applications", application_payload(workflow["id"], source["id"]))
        created.append("应用草稿")
    else:
        application = client.get(f"/api/v1/applications/{application['id']}")
    if application["status"] == "draft" and application["latestVersionStatus"] == "draft":
        application = client.post(f"/api/v1/applications/{application['id']}/publish", {
            "version": application["recordVersion"], "releaseVersion": application["latestVersion"],
        })
        created.append("应用发布版本")

    return {
        "workspaceId": client.workspace_id,
        "datasource": {"id": source["id"], "name": source["name"], "table": "t_cheliangxinxi1"},
        "createdThisRun": created,
        "alerts": {
            "deviceId": alert_device["id"], "pointCount": len(points), "adviceId": advice["id"],
            "ruleId": rule["id"], "workflowId": workflow["id"], "executionId": execution["id"],
            "reportId": report_id, "outcome": report["outcome"], "problemCount": len(problems),
        },
        "quality": {
            "deviceId": quality_device["id"], "metricIds": [item["id"] for item in metrics],
            "templateId": template["id"],
            "inspections": [{"id": item["id"], "sampleId": item["sampleId"], "outcome": item["outcome"], "effectiveOutcome": item["effectiveOutcome"]} for item in inspections],
        },
        "application": {"id": application["id"], "status": application["status"], "version": application["publishedVersion"]},
        "paths": {
            "alerts": "/alerts/devices", "quality": "/quality/inspections",
            "application": f"/applications?id={application['id']}", "operations": "/operations",
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过正式 API 添加设备预警、质检与应用示例")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default="workspace-admin")
    parser.add_argument("--workspace-id", default="")
    parser.add_argument("--source-name", default="variantorm")
    parser.add_argument("--allow-nonlocal", action="store_true", help="明确允许向非本机地址添加示例")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    parsed = urlparse(args.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit("--base-url 必须是有效的 http(s) 地址")
    if not args.allow_nonlocal and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("为避免误写远端环境，非本机地址必须显式添加 --allow-nonlocal")
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or _dotenv_value(ROOT / ".env.local", "BOOTSTRAP_ADMIN_PASSWORD")
    if not password:
        raise SystemExit("未找到 BOOTSTRAP_ADMIN_PASSWORD，请设置环境变量或 .env.local")
    client = ApiClient(args.base_url)
    workspace = client.login(args.username, password, args.workspace_id)
    try:
        result = seed(client, args.source_name)
    except RuntimeError as exc:
        print(f"示例创建失败：{exc}", file=sys.stderr)
        return 1
    result["workspaceName"] = workspace.get("name", "")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
