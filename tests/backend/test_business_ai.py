"""AI workbench actions reuse normal services, permissions and persisted resources.

中文说明：AI 工作台动作必须复用常规服务、权限与已持久化资源，而不是走旁路。
验证行为：动作目录/OpenAPI/集合是同一份类型化契约；大屏动作只保存一份已验证草稿并可重放；
模板填槽只填被锁定字段；对话在模型反复改参数时只交付一份草稿；数据源发现拆分复合业务关键词；
空发现写入一次性升级标记；HTTP 源只执行已保存的读请求；幻觉来源在创建回执前被拒。
隔离方式：client 夹具（内存 SQLite）+ monkeypatch 替换模型，不连业务库与外部模型。
"""
from __future__ import annotations

import json

import pytest

from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified

from app.models import AuthIdentity, DataSource, Run
from app.modules.analytics.models import QueryHistory, SourceSemanticDocument
from app.modules.business_ai.models import BusinessActionReceipt
from app.modules.reports.models import Report
from app.modules.reports.template_lock import visual_fingerprint
from app.modules.runtime import agent as runtime_agent
from app.providers import model
from app.providers.tool.gateway import ToolGateway, build_context
from business_worker_fakes import isolated_dsh_worker
from test_alerts import business_node, equipment, policy
from test_analytics import source
from test_collection import create_source as create_http_source, upstream
from test_reports import create_custom_layout_template, fake_generated
from test_system_foundation import activate_user, create_role, create_user
from test_workflows import create_flow, execute, graph, result

API = "/api/v1/ai/business-actions"
pytestmark = pytest.mark.usefixtures("approved_report_semantics")


def test_action_catalog_openapi_and_collection_are_one_typed_contract(client, administrator):
    response = client.get(API, headers=administrator)
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert {row["capabilityId"] for row in rows} == {
        "business.datasources.discover", "alerts.overview", "dashboard.generate",
    }
    assert all(row["available"] and row["requiredPermissions"] and row["inputSchema"] for row in rows)
    dashboard = next(row for row in rows if row["capabilityId"] == "dashboard.generate")
    assert dashboard["resultComponent"] == "dashboard"
    assert "templates.view" in dashboard["requiredPermissions"]
    assert "requestKey" in dashboard["inputSchema"]["properties"]
    assert "不公开发布" in dashboard["description"]

    spec = client.app.openapi()
    operations = {
        spec["paths"][API]["get"]["operationId"],
        spec["paths"][API + "/data-sources/discover"]["post"]["operationId"],
        spec["paths"][API + "/alerts/overview"]["post"]["operationId"],
        spec["paths"][API + "/dashboards/drafts"]["post"]["operationId"],
    }
    assert operations == {
        "ai_business_actions_list", "ai_business_datasources_discover",
        "ai_business_alerts_overview", "ai_business_dashboard_draft_create",
    }
    operation = spec["paths"][API + "/dashboards/drafts"]["post"]
    assert operation["summary"] and "requestKey" in operation["description"]
    assert "DashboardDraftInput" in spec["components"]["schemas"]

    collection = client.get("/api/v1/platform/api-collection", headers=administrator)
    assert collection.status_code == 200
    group = next(item for item in collection.json()["item"] if item["name"] == "AI 工作台业务能力")
    names = {item["name"] for item in group["item"]}
    assert "生成并保存真实数据数字大屏草稿" in names and "在 AI 工作台查看设备预警与问题" in names
    assert administrator["Authorization"].split(" ", 1)[1] not in collection.text


def test_dashboard_action_saves_one_verified_draft_and_replays_same_request(
    client, administrator, source, monkeypatch, db,
):
    calls = []

    def respond(*args, **kwargs):
        calls.append(kwargs)
        return {
            "content": json.dumps(fake_generated(source["id"])),
            "model": "isolated-business-action",
            "usage": {"total_tokens": 9},
        }

    monkeypatch.setattr(model, "chat_for_run", respond)
    payload = {
        "requestKey": "dashboard-business-001",
        "requirement": "生成财务和设备数字大屏，按部门展示真实金额",
        "templateMode": "none",
    }
    first = client.post(API + "/dashboards/drafts", headers=administrator, json=payload)
    assert first.status_code == 201, first.text
    view = first.json()["data"]
    assert view["status"] == "draft" and view["resultType"] == "dashboard"
    assert view["datasourceIds"] == [source["id"]] and view["queryIds"]
    workspace_query = "?workspace_id=" + administrator["X-Workspace-Id"]
    assert view["widgetCount"] == 1
    assert view["previewPath"] == "/report/view/" + view["reportId"] + workspace_query
    assert any(
        item["href"] == "/report/designer/" + view["reportId"] + workspace_query
        for item in view["actions"]
    )
    assert view["provenance"]["published"] is False and view["provenance"]["productionWriteback"] is False

    report = db.get(Report, view["reportId"])
    assert report is not None and report.status == "draft"
    assert report.lineage["entry"] == "ai_workbench"
    assert report.lineage["generationId"] == view["generationId"]
    assert report.datasource_ids == [source["id"]]
    assert json.loads(client.get("/api/v1/reports/" + report.id, headers=administrator).json()["data"]["content"])["widgets"]

    receipt = db.scalar(select(BusinessActionReceipt))
    legacy_result = dict(receipt.result_json)
    legacy_result["previewPath"] = "/report/view/" + view["reportId"]
    legacy_result["actions"] = [
        {**item, "href": item["href"].split("?", 1)[0]}
        for item in legacy_result["actions"]
    ]
    receipt.result_json = legacy_result
    db.commit()

    second = client.post(API + "/dashboards/drafts", headers=administrator, json=payload)
    assert second.status_code == 201, second.text
    replay = second.json()["data"]
    assert replay["reused"] is True and replay["reportId"] == view["reportId"]
    assert replay["previewPath"].endswith(workspace_query)
    assert all(
        item["href"].endswith(workspace_query)
        for item in replay["actions"]
        if item["href"].startswith("/report/")
    )
    assert len(calls) == 1
    assert len(db.scalars(select(Report)).all()) == 1
    assert len(db.scalars(select(BusinessActionReceipt)).all()) == 1

    conflict = client.post(API + "/dashboards/drafts", headers=administrator, json={
        **payload, "requirement": "换成另一份设备趋势大屏",
    })
    assert conflict.status_code == 409 and "防重复键" in conflict.text
    assert len(db.scalars(select(Report)).all()) == 1


def test_dashboard_action_uses_customer_json_to_fill_only_locked_template_data(
    client, administrator, source, monkeypatch, db,
):
    template = create_custom_layout_template(client, administrator, "客户需求锁定模板")
    slot_id = template["content"]["widgets"][0]["id"]
    requirement_document = {
        "schemaVersion": "dashboard-requirement-v1",
        "objective": "生成华北财务和设备经营大屏",
        "metrics": ["金额合计"],
        "dimensions": ["部门"],
        "filters": ["区域=华北"],
        "timeRange": "全部可用数据",
        "notes": "样式使用已确认模板",
    }
    contexts = []

    def respond(profile, messages, **kwargs):
        contexts.append(json.loads(messages[-1]["content"]))
        return {
            "content": json.dumps({
                "title": "华北财务与设备大屏",
                "templateId": template["id"],
                "bindings": [{
                    "widgetId": slot_id,
                    "datasourceId": source["id"],
                    "sql": "SELECT department, SUM(amount) AS total FROM sales GROUP BY department",
                    "parameters": {},
                }],
            }),
            "model": "customer-json-template-test",
            "usage": {"total_tokens": 11},
        }

    monkeypatch.setattr(model, "chat_for_run", respond)
    canvas = template["content"]["canvas"]
    response = client.post(API + "/dashboards/drafts", headers=administrator, json={
        "requestKey": "dashboard-customer-json-001",
        "requirementDocument": requirement_document,
        "title": "客户华北经营大屏",
        "datasourceIds": [source["id"]],
        "templateMode": "manual",
        "templateId": template["id"],
        "width": canvas["width"],
        "height": canvas["height"],
    })
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert result["templateId"] == template["id"]
    assert result["datasourceIds"] == [source["id"]]
    assert result["widgetCount"] == len(template["content"]["widgets"])
    assert len(contexts) == 1 and contexts[0]["customerRequirement"] == requirement_document

    report = db.get(Report, result["reportId"])
    assert report is not None and report.status == "draft"
    assert report.lineage["customerRequirement"] == requirement_document
    assert report.lineage["templateBindingContract"] == "data-only-v1"
    assert visual_fingerprint(report.content) == visual_fingerprint(template["content"])
    assert report.content["widgets"][0]["dataStatus"] == "verified"


def test_chat_dashboard_delivers_only_one_draft_when_model_refines_arguments(
    client, administrator, source, monkeypatch, db,
):
    calls = []

    def respond(*args, **kwargs):
        if "candidates" in json.loads(args[1][1]["content"]):
            return {"content": json.dumps({"templateId": None})}
        calls.append(kwargs)
        return {
            "content": json.dumps(fake_generated(source["id"])),
            "model": "isolated-business-action",
            "usage": {"total_tokens": 9},
        }

    monkeypatch.setattr(model, "chat_for_run", respond)
    identity = db.scalar(select(AuthIdentity).where(
        AuthIdentity.username_normalized == "workspace-admin",
    ))
    execution_context = build_context(
        db=db,
        workspace_id=administrator["X-Workspace-Id"],
        user_id=identity.user_id,
        run_id="chat-dashboard-once",
        selected_datasource_ids=[source["id"]],
    )
    gateway = ToolGateway(db)
    first = gateway.execute(administrator["X-Workspace-Id"], "create_business_dashboard", {
        "requirement": "生成财务销售数字大屏",
        "title": "财务销售数字大屏",
        "template_mode": "none",
    }, execution_context)
    second = gateway.execute(administrator["X-Workspace-Id"], "create_business_dashboard", {
        "requirement": "补充订单趋势并调整标题",
        "title": "财务销售大屏",
        "template_mode": "none",
    }, execution_context)

    assert first.status == "success" and second.status == "success"
    assert second.data["reused"] is True
    assert second.data["reportId"] == first.data["reportId"]
    assert len(calls) == 1
    assert len(db.scalars(select(Report)).all()) == 1
    assert len(db.scalars(select(BusinessActionReceipt)).all()) == 1


def test_data_source_discovery_splits_compound_business_keywords(
    client, administrator, source,
):
    response = client.post(API + "/data-sources/discover", headers=administrator, json={
        "keyword": "财务和分析",
        "limit": 20,
    })
    assert response.status_code == 200, response.text
    view = response.json()["data"]
    assert source["id"] in {row["id"] for row in view["sources"]}
    assert "组合关键词分别匹配" in view["selectionPolicy"]


def test_empty_discovery_persists_one_time_upgrade_for_legacy_sources(
    client, administrator, source, db,
):
    row = db.get(DataSource, source["id"])
    legacy_config = dict(row.connection_config or {})
    for key in (
        "autoDescription", "businessTableCount", "fieldCount", "tableCount",
        "commentedTableCount", "commentedFieldCount", "semanticIndexStatus",
        "semanticIndexedTableCount", "businessDomains", "metadataSyncedAt",
    ):
        legacy_config.pop(key, None)
    row.connection_config = legacy_config
    flag_modified(row, "connection_config")
    db.execute(delete(SourceSemanticDocument).where(
        SourceSemanticDocument.datasource_id == source["id"],
    ))
    db.commit()

    response = client.post(API + "/data-sources/discover", headers=administrator, json={
        "keyword": "",
        "limit": 20,
    })
    assert response.status_code == 200, response.text
    candidate = next(item for item in response.json()["data"]["sources"] if item["id"] == source["id"])
    assert candidate["descriptionSource"] == "metadata"
    assert candidate["description"]
    assert candidate["businessTableCount"] == 1
    assert candidate["semanticIndexedTableCount"] == 1
    assert candidate["semanticIndexStatus"] in {"ready", "keyword_only"}

    db.expire_all()
    persisted = db.scalar(select(SourceSemanticDocument).where(
        SourceSemanticDocument.datasource_id == source["id"],
    ))
    persisted_source = db.get(DataSource, source["id"])
    assert persisted is not None
    assert persisted_source.connection_config["semanticIndexedTableCount"] == 1
    assert persisted_source.connection_config["autoDescription"]


def test_http_source_is_discoverable_selectable_and_executes_only_saved_read_request(
    client, administrator, upstream, monkeypatch, db, isolated_dsh_worker,
):
    http_source, _ = create_http_source(
        client, administrator, upstream,
        name="财务与设备实时指标接口",
        description="读取销售金额、设备状态和质检指标，仅用于经营分析",
        allowAutomatic=True,
        fields=[
            {"name": "设备", "path": "$.device", "type": "string", "comment": "设备编号"},
            {"name": "金额", "path": "$.amount", "type": "decimal", "comment": "销售金额"},
            {"name": "启用", "path": "$.enabled", "type": "boolean", "comment": "设备启用状态"},
        ],
    )

    discovered = client.post(API + "/data-sources/discover", headers=administrator, json={
        "keyword": "财务设备",
        "limit": 20,
    })
    assert discovered.status_code == 200, discovered.text
    candidate = next(
        item for item in discovered.json()["data"]["sources"]
        if item["id"] == http_source["id"]
    )
    assert candidate["provider"] == "http" and candidate["resourceType"] == "http"
    assert candidate["descriptionSource"] == "configured"
    assert candidate["description"] == "读取销售金额、设备状态和质检指标，仅用于经营分析"
    assert candidate["automaticReady"] is True
    assert candidate["fieldCount"] == 3
    assert candidate["tables"][0]["retrievalEvidence"] == {
        "recordPath": "$.items[*]", "method": "GET",
    }
    assert candidate["tables"][0]["fields"][1]["description"] == "销售金额"
    assert not upstream[1], "data-source discovery must never send the saved HTTP request"

    conversation = client.post('/api/chat/conversations', headers=administrator,
                               json={'title': 'HTTP 数据分析'}).json()
    prepared = isolated_dsh_worker.prepare(administrator, conversation['id'], '分析财务和设备数据',
        datasource_ids=[http_source['id']], dashboard_template_mode='none')
    assert http_source['id'] in prepared['system_prompt']
    assert prepared['resources']['datasource_ids'] == [http_source['id']]
    assert prepared['resources']['dashboard_template_mode'] == 'none'
    assert not upstream[1], 'DSH 建单不能提前执行已选 HTTP 数据源'

    execution_context = build_context(
        db=db,
        workspace_id=administrator["X-Workspace-Id"],
        user_id=db.scalar(select(AuthIdentity).where(
            AuthIdentity.username_normalized == "workspace-admin",
        )).user_id,
        run_id="http-business-query",
        selected_datasource_ids=[http_source["id"]],
    )
    gateway = ToolGateway(db)
    rejected_sql = gateway.execute(
        administrator["X-Workspace-Id"], "query_business_data",
        {"query": "SELECT * FROM remote", "mode": "sql"}, execution_context,
    )
    assert rejected_sql.status == "failed" and "HTTP/API 数据源不执行 SQL" in rejected_sql.error_summary
    assert not upstream[1], "rejected HTTP SQL must not send a request"

    business_question = "读取当前接口的财务和设备指标"
    queried = gateway.execute(
        administrator["X-Workspace-Id"], "query_business_data",
        {"query": business_question, "mode": "natural_language", "max_rows": 1},
        execution_context,
    )
    assert queried.status == "success", queried.error_summary
    assert queried.data["queryType"] == "http"
    assert queried.data["generatedSql"] == ""
    assert queried.data["data"] == [{"设备": "A", "金额": "123.450", "启用": False}]
    assert queried.data["rowOrigins"] == [0]
    assert len(upstream[1]) == 1
    history = db.get(QueryHistory, queried.data["queryId"])
    assert history.query_text == business_question


def test_chat_pins_selected_datasource_into_run_and_agent_tool_context(
    client, administrator, source, isolated_dsh_worker,
):
    """DSH 建单和刷新后保留显式数据源与无模板选择。"""
    conversation = client.post('/api/chat/conversations', headers=administrator, json={'title': '数据源绑定'}).json()
    prepared = isolated_dsh_worker.prepare(administrator, conversation['id'], '分析财务数据',
        datasource_ids=[source['id']], dashboard_template_mode='none')
    assert source['id'] in prepared['system_prompt']
    assert prepared['resources']['datasource_ids'] == [source['id']]
    assert prepared['resources']['dashboard_template_mode'] == 'none'
    assert 'query_business_data' in {tool['name'] for tool in prepared['tools']}
    isolated_dsh_worker.settle(prepared['run_id'], 'completed', '已绑定所选数据源。')
    detail = isolated_dsh_worker.history(administrator, conversation['id'])
    snapshot = detail['last_run']['context_snapshot']
    assert snapshot['datasource_ids'] == [source['id']]
    assert snapshot['dashboard_template_mode'] == 'none'
    assert snapshot['dashboard_template_id'] is None


def test_dashboard_rejects_hallucinated_source_before_creating_receipt(
    client, administrator, source, db,
):
    response = client.post(API + "/dashboards/drafts", headers=administrator, json={
        "requestKey": "dashboard-invalid-source-001",
        "requirement": "生成财务和设备数字大屏",
        "datasourceIds": ["not-a-real-data-source"],
        "templateMode": "none",
    })
    assert response.status_code == 422, response.text
    payload = response.json()
    assert "数据源发现结果" in payload["message"]
    assert payload["invalid_datasource_ids"] == ["not-a-real-data-source"]
    assert {row["id"] for row in payload["available_datasources"]} == {source["id"]}
    assert db.scalar(select(BusinessActionReceipt.id).where(
        BusinessActionReceipt.request_key == "dashboard-invalid-source-001",
    )) is None


def test_dashboard_retries_once_when_model_first_returns_non_json(
    client, administrator, source, monkeypatch,
):
    responses = iter([
        {"content": "我来为你生成大屏。", "model": "retry-test"},
        {
            "content": "```json\n" + json.dumps(fake_generated(source["id"]), ensure_ascii=False) + "\n```",
            "model": "retry-test",
            "usage": {"total_tokens": 12},
        },
    ])
    calls = []

    def respond(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return next(responses)

    monkeypatch.setattr(model, "chat_for_run", respond)
    response = client.post(API + "/dashboards/drafts", headers=administrator, json={
        "requestKey": "dashboard-json-retry-001",
        "requirement": "生成财务和设备数字大屏",
        "templateMode": "none",
    })
    assert response.status_code == 201, response.text
    assert response.json()["data"]["datasourceIds"] == [source["id"]]
    assert len(calls) == 2
    assert calls[0]["kwargs"]["temperature"] == 0.3
    assert calls[1]["kwargs"]["temperature"] == 0
    assert "上一输出不是可解析的 JSON" in calls[1]["args"][1][-1]["content"]


def test_alert_action_returns_existing_evidence_without_rerunning_workflow(client, administrator, db):
    rule, _ = policy(client, administrator, equipment(client, administrator))
    flow = create_flow(client, administrator, graph(business_node(rule)))
    execution = result(execute(client, administrator, flow, {
        "data": [{"device": "EQ-01", "amount": 220}],
    }))
    report_id = execution["result"]["outputs"]["end"]["alertReportId"]
    run_count = len(db.scalars(select(Run)).all())

    response = client.post(API + "/alerts/overview", headers=administrator, json={"limit": 20})
    assert response.status_code == 200, response.text
    view = response.json()["data"]
    assert view["resultType"] == "alert_problem_list"
    assert view["summary"]["returnedReports"] == 1 and view["summary"]["returnedProblems"] == 1
    assert view["reports"][0]["id"] == report_id
    assert view["reports"][0]["href"] == "/alerts/reports?report=" + report_id
    assert view["problems"][0]["recordKey"] == "EQ-01"
    assert view["provenance"]["source"].endswith("未重新请求设备")
    assert len(db.scalars(select(Run)).all()) == run_count


def test_chat_agent_executes_and_persists_structured_alert_result(
    client, administrator, isolated_dsh_worker,
):
    """原生 Worker 完成工具执行和结算后，刷新重建同一预警卡片和运行轨迹。"""
    conversation = client.post('/api/chat/conversations', headers=administrator, json={'title': '设备预警查询'}).json()
    prepared = isolated_dsh_worker.prepare(administrator, conversation['id'], '查看设备预警')
    result = isolated_dsh_worker.execute(prepared['run_id'], 'call-alert-overview', 1, 'view_device_alerts', {})
    assert result['status'] == 'success'
    event = isolated_dsh_worker.append_event(prepared['run_id'], 'tool.completed',
        {'call_id': 'call-alert-overview', 'tool_name': 'view_device_alerts', 'result': result})
    assert event['data']['tool_name'] == 'view_device_alerts'
    isolated_dsh_worker.settle(prepared['run_id'], 'completed', '已读取当前权限范围内的设备预警。')
    detail = isolated_dsh_worker.history(administrator, conversation['id'])
    assistant = detail['messages'][-1]
    assert assistant['role'] == 'assistant'
    result_parts = [part for part in assistant['parts'] if part['type'] == 'tool_result']
    assert len(result_parts) == 1
    assert result_parts[0]['tool'] == 'view_device_alerts'
    assert result_parts[0]['result']['status'] == 'success'
    assert result_parts[0]['result']['data']['resultType'] == 'alert_problem_list'
    trace = client.get(f"/api/chat/runs/{prepared['run_id']}/trace", headers=administrator)
    assert trace.status_code == 200, trace.text
    assert trace.json()['run']['status'] == 'completed'
    assert trace.json()['tool_calls'][0]['tool_name'] == 'view_device_alerts'
    assert trace.json()['tool_calls'][0]['status'] == 'completed'
    assert {'view_device_alerts', 'create_business_dashboard'}.issubset({tool['name'] for tool in prepared['tools']})


def test_presentation_stage_gate_only_activates_for_an_explicit_ppt_run():
    tools = [{
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object"}},
    } for name in (
        "create_presentation_plan", "write_presentation_slides",
        "finalize_presentation", "view_device_alerts",
    )]
    ordinary, action, missing = runtime_agent._presentation_stage_tools(
        tools, "isolated-workspace", "ordinary-business-run",
        presentation_requested=False,
    )
    assert ordinary == tools and action is None and missing == []

    presentation, action, missing = runtime_agent._presentation_stage_tools(
        tools, "isolated-workspace", "selected-ppt-run",
        presentation_requested=True,
    )
    assert [item["function"]["name"] for item in presentation] == ["create_presentation_plan"]
    assert action == "create_presentation_plan" and missing == []


def test_tool_gateway_filters_and_rechecks_business_permissions(client, administrator, db, monkeypatch):
    role = create_role(client, administrator, "alert_ai_reader", [
        "home.view", "chat.execute", "alerts.view",
    ], "workspace")
    user = create_user(client, administrator, [role["id"]], username="alert-ai-reader")
    headers = activate_user(client, "alert-ai-reader")
    me = client.get("/api/auth/me", headers=headers).json()
    execution_context = build_context(
        db=db, workspace_id=administrator["X-Workspace-Id"], user_id=user["id"],
        run_id="permission-check-run",
    )
    gateway = ToolGateway(db)
    names = {spec.name for spec in gateway.list_available(
        administrator["X-Workspace-Id"], execution_context=execution_context,
    )}
    assert "view_device_alerts" in names
    assert "create_business_dashboard" not in names
    assert "list_business_data_sources" not in names

    model_called = False

    def forbidden_model(*args, **kwargs):
        nonlocal model_called
        model_called = True
        raise AssertionError("permission denial must happen before any model call")

    monkeypatch.setattr(model, "chat_for_run", forbidden_model)
    denied = gateway.execute(administrator["X-Workspace-Id"], "create_business_dashboard", {
        "requirement": "生成财务和设备数字大屏", "template_mode": "none",
    }, execution_context)
    assert denied.status == "failed" and denied.error_code == "forbidden" and not model_called
    allowed = gateway.execute(administrator["X-Workspace-Id"], "view_device_alerts", {}, execution_context)
    assert allowed.status == "success" and allowed.data["resultType"] == "alert_problem_list"

    db.rollback()
    changed = client.patch("/api/v1/system/roles/" + role["id"], headers=administrator, json={
        "version": role["version"], "permissions": ["home.view", "chat.execute"],
    })
    assert changed.status_code == 200, changed.text
    revoked = gateway.execute(administrator["X-Workspace-Id"], "view_device_alerts", {}, execution_context)
    assert revoked.status == "failed" and revoked.error_code == "forbidden"
