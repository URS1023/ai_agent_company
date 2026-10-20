"""工具契约只复用已授权的业务读取接口；验证不启动服务、不连接数据库。"""

from copy import deepcopy

import pytest

from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.modules.integrations.business_tools import business_tool_spec


def test_tool_spec_filters_operations_and_resolves_only_referenced_schemas():
    source = {
        "paths": {
            "/api/v1/devices": {
                "get": {"parameters": [{"schema": {"$ref": "#/components/schemas/Page"}}]},
                "post": {"operationId": "create_device"},
            },
            "/api/v1/inspections": {"get": {"summary": "质量"}},
            "/api/v1/secret-references": {"get": {}},
            "/api/v1/devices/{id}/telemetry": {"get": {}},
        },
        "components": {"schemas": {
            "Page": {"properties": {"next": {"$ref": "#/components/schemas/Page"}}},
            "Secret": {"type": "string"},
        }},
    }
    unchanged = deepcopy(source)
    result = business_tool_spec(source, Actor("u", "w", frozenset({"m02.read", "m10.read"})))
    assert set(result["paths"]) == {"/api/v1/devices"}
    assert set(result["paths"]["/api/v1/devices"]) == {"get"}
    assert result["paths"]["/api/v1/devices"]["get"]["operationId"] == "list_devices"
    assert set(result["components"]["schemas"]) == {"Page"}
    assert result["paths"]["/api/v1/devices"]["get"]["security"] == [{"PlatformToken": []}]
    assert source == unchanged


def test_tool_spec_requires_chat_access_and_tracks_revoked_business_permissions():
    source = {"paths": {"/api/v1/devices": {"get": {}}}}
    with pytest.raises(DomainError) as denied:
        business_tool_spec(source, Actor("u", "w", frozenset({"m10.read"})))
    assert denied.value.status == 403
    assert business_tool_spec(source, Actor("u", "w", frozenset({"m02.read"})))["paths"] == {}
