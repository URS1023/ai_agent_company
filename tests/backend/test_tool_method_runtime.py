"""Governance disabling applies to live gateways, including cached tool specs."""
import pytest

from app.providers.tool.base import ToolResult
from app.providers.tool.gateway import ToolGateway, build_context
from test_capabilities_center import API, create_http, result


@pytest.mark.parametrize("name", ["current_datetime", "factory.device_status"])
def test_method_disable_is_live_for_discovery_execution_and_cached_specs(client, administrator, db, monkeypatch, name):
    if name == "factory.device_status":
        created = create_http(client, administrator)
        result(client.post(API + f"/connectors/{created['id']}/publish", headers=administrator,
                           json={"version": created["version"]}))
    user = client.get("/api/auth/me", headers=administrator).json()["user"]
    ctx = build_context(db=db, workspace_id=administrator["X-Workspace-Id"], user_id=user["id"], run_id="method-governance")
    gateway = ToolGateway(db)
    called = []
    def dispatch(spec, arguments, tool_name, context):
        called.append(tool_name)
        return ToolResult(status="success", data={"executed": True})
    monkeypatch.setattr(gateway, "_dispatch", dispatch)
    cached = gateway.resolve(ctx["workspace_id"], name, execution_context=ctx)
    assert cached is not None
    arguments = {} if name == "current_datetime" else {"deviceCode": "A"}
    assert gateway.execute(ctx["workspace_id"], name, arguments, ctx).status == "success"
    methods = result(client.get(API + "/methods", headers=administrator))["items"]
    current = next(item for item in methods if item["name"] == name)
    payload = {"version": current["methodVersion"], "code": name, "displayName": name,
               "description": "运行时停用验收", "enabled": False, "tags": []}
    disabled = result(client.patch(API + "/methods/" + name, headers=administrator, json=payload))
    for context in (None, ctx):
        assert name not in {spec.name for spec in gateway.list_available(ctx["workspace_id"], execution_context=context)}
    assert gateway.execute(ctx["workspace_id"], name, arguments, ctx).status == "failed"
    assert gateway.execute_spec(cached, arguments, getattr(cached, "_connector_spec", {}), ctx).status == "failed"
    assert called == [name]
    result(client.patch(API + "/methods/" + name, headers=administrator,
                        json={**payload, "version": disabled["methodVersion"], "enabled": True}))
    assert name in {spec.name for spec in gateway.list_available(ctx["workspace_id"], execution_context=ctx)}
    assert gateway.execute(ctx["workspace_id"], name, arguments, ctx).status == "success"
    assert called == [name, name]


def test_published_workflow_cannot_call_method_disabled_after_publish(client, administrator, monkeypatch):
    from uuid import uuid4
    from test_workflows import API as FLOWS, create_flow, graph, node
    flow = create_flow(client, administrator,
                       graph(node("clock", "tool", {"toolName": "current_datetime", "arguments": {}})))
    flow = result(client.post(FLOWS + f"/{flow['id']}/publish", headers=administrator,
                              json={"version": flow["version"]}))
    methods = result(client.get(API + "/methods", headers=administrator))["items"]
    current = next(item for item in methods if item["name"] == "current_datetime")
    result(client.patch(API + "/methods/current_datetime", headers=administrator, json={
        "version": current["methodVersion"], "code": "current_datetime", "displayName": "当前时间",
        "description": "发布后停用", "enabled": False, "tags": [],
    }))
    calls = []
    monkeypatch.setattr(ToolGateway, "_dispatch", lambda *args: calls.append(args))
    response = client.post(FLOWS + f"/{flow['id']}/runs", headers=administrator, json={
        "version": flow["version"], "mode": "published", "idempotencyKey": uuid4().hex, "inputs": {},
    })
    assert response.status_code == 422, response.text
    assert calls == []
