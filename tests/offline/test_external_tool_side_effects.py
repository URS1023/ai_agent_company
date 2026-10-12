"""外部 HTTP/MCP 工具的幂等键与不确定回执离线回归。

所有远端交互均由内存桩代替，不连接互联网、不写业务数据库；重点验证请求键
稳定传递，以及连接在副作用后断开时不会返回可自动重做的普通失败。
"""
from __future__ import annotations

import asyncio

import pytest

from app.errors import ToolExecutionError
from app.providers.tool import http_provider, mcp_provider
from app.providers.tool.base import ToolSpec
from app.providers.tool.side_effects import idempotency_key


def _spec(name: str = "factory.write") -> ToolSpec:
    return ToolSpec(name=name, description="测试工具", provider="http", input_schema={"type": "object"})


def _ctx(**extra):
    return {"allow_network": True, "run_id": "run-001", "connector_version_id": "cv-001", **extra}


def test_idempotency_key_is_stable_and_prefers_persisted_call_key():
    first = idempotency_key(_ctx(), _spec(), {"value": 1})
    second = idempotency_key(_ctx(), _spec(), {"value": 1})
    assert first == second and first.startswith("tool-")
    assert idempotency_key(_ctx(idempotency_key="dsh-call-001"), _spec(), {}) == "dsh-call-001"


def test_http_post_passes_key_and_marks_confirmed(monkeypatch):
    observed = {}

    def fake_request(config, body, credentials, **kwargs):
        observed["headers"] = dict(config.headers)
        return {"accepted": True}, {"httpStatus": 200, "responseBytes": 17}

    monkeypatch.setattr(http_provider, "request_json", fake_request)
    result = http_provider.invoke(
        _spec(), {"value": 1},
        _ctx(connector_spec={"base_url": "https://example.com", "path": "/write", "method": "POST",
                             "body_mapping": "json", "headers": {}, "auth": {"type": "none"}}),
    )
    assert result.status == "success"
    assert observed["headers"]["Idempotency-Key"] == result.metadata["idempotencyKey"]
    assert result.metadata["operationOutcome"] == "confirmed"


def test_http_post_unknown_receipt_is_not_retryable(monkeypatch):
    def ambiguous(*_args, **_kwargs):
        raise ToolExecutionError("连接在回执前断开", operation_outcome="uncertain")

    monkeypatch.setattr(http_provider, "request_json", ambiguous)
    result = http_provider.invoke(
        _spec(), {"value": 1},
        _ctx(connector_spec={"base_url": "https://example.com", "path": "/write", "method": "POST",
                             "body_mapping": "json", "headers": {}, "auth": {"type": "none"}}),
    )
    assert result.status == "failed"
    assert result.error_code == "tool_result_unconfirmed"
    assert result.metadata["operationOutcome"] == "uncertain"
    assert result.metadata["retryable"] is False
    assert result.metadata["requiresVerification"] is True


def test_http_get_transport_failure_keeps_known_failure(monkeypatch):
    monkeypatch.setattr(http_provider, "request_json",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(ToolExecutionError("解析失败")))
    with pytest.raises(ToolExecutionError):
        http_provider.invoke(
            ToolSpec(name="factory.read", description="测试工具", provider="http", input_schema={"type": "object"}), {},
            _ctx(connector_spec={"base_url": "https://example.com", "path": "/read", "method": "GET",
                                 "body_mapping": "query", "headers": {}, "auth": {"type": "none"}}),
        )


def test_mcp_call_unknown_receipt_is_not_retryable(monkeypatch):
    class Session:
        async def call_tool(self, **_kwargs):
            raise RuntimeError("stream closed after send")

    async def fake_connect(*_args, **_kwargs):
        return object(), Session(), object(), {}

    async def fake_close(*_args, **_kwargs):
        return None

    original_connect, original_close = mcp_provider._connect, mcp_provider._close
    try:
        mcp_provider._connect, mcp_provider._close = fake_connect, fake_close
        spec = ToolSpec(name="factory.mutate", description="测试工具", provider="mcp", input_schema={"type": "object"})
        result = asyncio.run(mcp_provider._call_tool({}, spec, {"value": 1}, _ctx()))
    finally:
        mcp_provider._connect, mcp_provider._close = original_connect, original_close
    assert result.status == "failed"
    assert result.error_code == "tool_result_unconfirmed"
    assert result.metadata["operationOutcome"] == "uncertain"
    assert result.metadata["retryable"] is False


def test_mcp_request_headers_carry_stable_key_without_exposing_arguments():
    headers, _ = mcp_provider._request_headers(
        {"headers": {}, "auth": {"type": "none"}},
        _ctx(),
        "dsh-call-001",
    )
    assert headers["Idempotency-Key"] == "dsh-call-001"
    assert "value" not in str(headers)
