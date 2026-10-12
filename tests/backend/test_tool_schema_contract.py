"""验证保存的工具约束在派发前生效，全部用内存参数与隔离接口。"""
from __future__ import annotations

import json
import pytest

from app.errors import SchemaValidationError
from app.providers.tool.validator import RuleValidator, rule_validators, validate_arguments, validate_schema, validate_value
from test_capabilities_center import API, http_payload


@pytest.mark.parametrize(("value", "constraint"), [
    (2, {"enum": [1, 3]}),
    (True, {"enum": [1]}),
    (True, {"const": 1}),
    ({"ok": True}, {"const": {"ok": 1}}),
    (None, {"enum": [0]}),
    (2, {"allOf": [{"minimum": 3}]}),
    (2, {"not": {"const": 2}}),
    (0, {"exclusiveMinimum": 0}),
    (10, {"exclusiveMaximum": 10}),
    (0.31, {"multipleOf": 0.1}),
    ([{"a": 1}, {"a": 1}], {"uniqueItems": True}),
    ({"extra": 1}, {"additionalProperties": {"type": "string"}}),
    ("2026-02-30", {"type": "string", "format": "date"}),
    ("2026-10-04", {"type": "string", "format": "date-time"}),
    ("https://bad host/", {"type": "string", "format": "uri"}),
    ("https://host:abc/", {"type": "string", "format": "uri"}),
    (float("nan"), {"type": "number"}),
    (float("inf"), {"type": "number"}),
    ({"extra": 1}, {"anyOf": [{"type": "object"}], "additionalProperties": False}),
    ({}, {"anyOf": [{"type": "object"}], "required": ["id"]}),
    (1, {"anyOf": [{"type": "integer"}], "minimum": 2}),
])
def test_invalid_values_cannot_bypass_declared_constraints(value, constraint):
    """约束组合也必须执行同级规则，不能命中一个分支就提前成功。"""
    with pytest.raises(SchemaValidationError):
        validate_arguments({"value": value}, {
            "type": "object", "properties": {"value": constraint}, "required": ["value"],
        })


@pytest.mark.parametrize(("value", "constraint"), [
    (3, {"enum": [1, 3]}),
    (False, {"const": False}),
    (None, {"enum": [None]}),
    (2, {"allOf": [{"minimum": 1}, {"maximum": 3}]}),
    (2, {"not": {"const": 3}}),
    (0.3, {"multipleOf": 0.1}),
    ([True, 1], {"uniqueItems": True}),
    ({"extra": "ok"}, {"additionalProperties": {"type": "string"}}),
    ("2024-02-29", {"type": "string", "format": "date"}),
    ("2026-10-04T09:30:00", {"type": "string", "format": "date-time"}),
    ("https://example.invalid/path?q=one", {"type": "string", "format": "uri"}),
    ({"id": 1}, {"anyOf": [{"type": "object"}], "required": ["id"]}),
])
def test_valid_values_keep_json_semantics(value, constraint):
    """布尔值与数字分离，十进制倍数不受二进制浮点误差影响。"""
    original = {"value": value}
    assert validate_arguments(original, {"type": "object", "properties": {"value": constraint}}) == original


@pytest.mark.parametrize("value", ["[]", "null", "1", '"text"'])
def test_root_arguments_must_stay_an_object_even_without_schema(value):
    with pytest.raises(SchemaValidationError):
        validate_arguments(value, {})


def test_type_error_does_not_copy_private_argument_into_feedback():
    secret = "SYNTHETIC-PRIVATE-TOKEN"
    with pytest.raises(SchemaValidationError) as caught:
        validate_arguments({"token": secret}, {
            "type": "object", "properties": {"token": {"type": "integer"}},
        })
    assert secret not in str(caught.value)
    assert "$arguments.token" in str(caught.value)


@pytest.mark.parametrize("container", [False, True])
@pytest.mark.parametrize("raw", ['{"x":' + "1" * 5000 + "}", "[" * 2000 + "]" * 2000],
    ids=["oversized-integer", "deep-container"])
def test_extreme_json_is_a_validation_error_not_a_server_error(raw, container):
    """数字解析上限与递归上限均是坏输入，不能以未处理异常逃出工具网关。"""
    with pytest.raises(SchemaValidationError):
        validate_arguments({"body": raw} if container else raw, {
            "type": "object", "properties": {"body": {"type": "object"}},
        })


def test_business_rule_runs_once_per_matching_value():
    seen = []
    rules = RuleValidator()
    rules.register("capture", lambda value, schema, context: seen.append(value))
    validate_value({"code": "A"}, {
        "type": "object", "properties": {"code": {"type": "string", "x-rule": "capture"}},
    }, rule_validators=rules)
    assert seen == ["A"]


def test_every_builtin_tool_schema_uses_supported_constraints():
    """遍历实际注册工具，避免收紧自定义工具契约时误伤内置能力。"""
    from app.providers.tool.builtin_provider import builtin_specs
    for tool in builtin_specs():
        try:
            validate_schema(tool.input_schema, rule_validators)
        except SchemaValidationError as exc:
            pytest.fail(f"{tool.name}: {exc}")


def test_schema_cache_does_not_trust_mutation_or_removed_rule():
    """缓存按完整内容和当前规则目录失效，不按可变对象身份放行。"""
    rules = RuleValidator()
    rules.register("check", lambda *_: None)
    schema = {"type": "string", "x-rule": "check"}
    validate_schema(schema, rules)
    rules._registry.clear()
    with pytest.raises(SchemaValidationError):
        validate_schema(schema, rules)
    schema = {"type": "integer"}
    validate_schema(schema)
    schema["multipleOf"] = 0
    with pytest.raises(SchemaValidationError):
        validate_schema(schema)


def test_invalid_unicode_is_reported_without_breaking_error_serialization():
    with pytest.raises(SchemaValidationError):
        validate_schema({"type": "object", "description": "\ud800"})
    with pytest.raises(SchemaValidationError):
        validate_arguments({"text": "\ud800"}, {"type": "object"})


def test_invalid_unicode_schema_returns_http_validation_error(client, administrator):
    payload = http_payload()
    payload["tools"][0]["inputSchema"]["description"] = "\ud800"
    response = client.post(API + "/connectors",
        headers={**administrator, "Content-Type": "application/json"},
        content=json.dumps(payload, ensure_ascii=True).encode("ascii"))
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("constraint", [
    {"type": "bogus"}, {"required": "id"}, {"enum": []}, {"multipleOf": 0},
    {"additionalProperties": {"$ref": "https://outside.example/schema"}},
    {"x-rule": "unregistered_rule"}, {"not": 3}, {"uniqueItems": "true"}, {"format": []},
    {"x-lookup": 123}, {"x-must-resolve": "false"}, {"x-must-resolve": True},
])
def test_invalid_schema_is_rejected_when_creating_connector(client, administrator, constraint):
    payload = http_payload()
    payload["tools"][0]["inputSchema"]["properties"]["deviceCode"] = constraint
    response = client.post(API + "/connectors", headers=administrator, json=payload)
    assert response.status_code == 422, response.text


def test_argument_compilation_rejects_invalid_contract_before_model(monkeypatch):
    from app.providers.tool import compiler
    from app.providers.tool.base import ToolSpec
    called = []
    monkeypatch.setattr(compiler, "chat_for_run", lambda *args, **kwargs: called.append(args))
    spec = ToolSpec(name="invalid", description="无效契约", provider="http", input_schema={
        "type": "object", "properties": {"id": {"x-lookup": 123}},
    })
    with pytest.raises(SchemaValidationError):
        compiler.compile_arguments(spec, "读取数据")
    assert called == []


def test_failed_argument_check_never_reaches_provider(client, administrator, monkeypatch):
    payload = http_payload()
    payload["tools"][0]["inputSchema"] = {
        "type": "object", "anyOf": [{"type": "object"}],
        "properties": {"deviceCode": {"type": "string"}},
        "required": ["deviceCode"], "additionalProperties": False,
    }
    created = client.post(API + "/connectors", headers=administrator, json=payload)
    assert created.status_code == 201, created.text
    connector_id = created.json()["data"]["id"]
    dispatched = []
    monkeypatch.setattr("app.providers.tool.gateway.ToolGateway._dispatch", lambda *args: dispatched.append(args))
    response = client.post(API + f"/connectors/{connector_id}/test", headers=administrator,
        json={"version": 1, "toolName": "factory.device_status", "arguments": {"unexpected": "A"}})
    assert response.status_code == 422, response.text
    assert dispatched == []
