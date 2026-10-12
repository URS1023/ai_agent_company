"""Offline contract checks for workflow HTTP overrides and rendering.

中文说明：工作流 HTTP 节点的覆盖参数与结果渲染契约（离线）。
隔离方式：monkeypatch + 桩响应，不发真实请求、不连数据库。
边界：请求头/参数覆盖必须显式生效，渲染不泄露内部字段。
"""

import time
from types import SimpleNamespace

import pytest

from app.errors import ValidationError as AppValidationError


def test_http_override_contract_rejects_empty_targets_and_secret_query_values():
    from app.modules.workflows.schemas import HttpConfig

    config = HttpConfig.model_validate({
        "datasourceId": "src_1",
        "url": "https://api.example.test/items/{{ $.itemId }}",
        "params": {"page": "{{ $.page }}"},
        "headers": {"X-Trace": "{{ $.traceId }}"},
        "body": {"itemId": "{{ $.itemId }}"},
    })
    assert config.url and config.params["page"] == "{{ $.page }}"

    with pytest.raises(ValueError, match="String should have at least 1 character"):
        HttpConfig.model_validate({"datasourceId": "src_1", "url": ""})
    with pytest.raises(ValueError, match="认证参数不能放入 URL"):
        HttpConfig.model_validate({"datasourceId": "src_1", "params": {"access_token": "x"}})
    with pytest.raises(AppValidationError, match="敏感请求体字段"):
        HttpConfig.model_validate({"datasourceId": "src_1", "body": {"token": "plain"}})


def test_http_templates_render_params_and_private_target_guard_with_real_jsonpath():
    from app.modules.workflows.nodes import _contains_secret_reference, _http_url, _literal_private_host
    from app.modules.workflows.expressions import RuleBudget

    budget = RuleBudget(time.monotonic() + 5)
    url = _http_url(
        "https://api.example.test/items?fixed=yes",
        {"page": "{{ $.page }}", "include": "{{ $.include }}"},
        {"page": 2, "include": True}, budget,
    )
    assert "page=2" in url and "include=True" in url and "fixed=yes" in url
    assert _literal_private_host("http://127.0.0.1:8080/items")
    assert _literal_private_host("http://localhost/items")
    assert not _literal_private_host("https://api.example.test/items")
    assert _contains_secret_reference({"Authorization": "Bearer ${secret.apiKey}"})
    assert not _contains_secret_reference({"X-Trace": "trace-1"})


def test_http_target_override_cannot_carry_bound_credentials(monkeypatch):
    from app.modules.collection.schemas import HttpSourceInput
    from app.modules.collection import sources
    from app.modules.workflows import nodes
    from app.modules.workflows.expressions import RuleBudget
    from app.modules.workflows.schemas import HttpConfig

    saved = HttpSourceInput.model_validate({
        "name": "bound source", "url": "https://api.example.test/items",
        "headers": {"Authorization": "Bearer ${secret.apiKey}"},
        "allowAutomatic": True,
    })
    source = SimpleNamespace(id="src_1")
    permissions: list[str] = []
    monkeypatch.setattr(sources, "http_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(sources, "source_config", lambda _source: saved)
    monkeypatch.setattr(nodes, "require_permission", lambda _ctx, permission: permissions.append(permission))

    config = HttpConfig.model_validate({
        "datasourceId": "src_1", "url": "https://other.example.test/items",
    })
    with pytest.raises(AppValidationError, match="不能把绑定数据源凭据"):
        nodes._execute_http(SimpleNamespace(), config, {}, RuleBudget(time.monotonic() + 5))
    assert permissions == ["datasources.publish"]


def test_http_write_retry_is_rejected_even_when_writes_are_confirmed(monkeypatch):
    from app.modules.collection.schemas import HttpSourceInput
    from app.modules.collection import sources
    from app.modules.workflows import nodes
    from app.modules.workflows.expressions import RuleBudget
    from app.modules.workflows.schemas import HttpConfig

    saved = HttpSourceInput.model_validate({
        "name": "write source", "url": "https://api.example.test/items", "method": "POST",
        "bodies": [{"item": 1}], "allowAutomatic": True,
    })
    monkeypatch.setattr(sources, "http_source", lambda *_args, **_kwargs: SimpleNamespace(id="src_1"))
    monkeypatch.setattr(sources, "source_config", lambda _source: saved)
    monkeypatch.setattr(sources, "credentials_for", lambda *_args: {})
    monkeypatch.setattr(nodes, "require_permission", lambda *_args: None)
    monkeypatch.setattr(nodes.collection, "collect", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("a confirmed write must fail before sending when retry is configured")))

    config = HttpConfig.model_validate({
        "datasourceId": "src_1", "retry": 1, "confirmWrites": True,
    })
    with pytest.raises(AppValidationError, match="不支持自动重试"):
        nodes._execute_http(None, config, {}, RuleBudget(time.monotonic() + 5))


def test_collection_execution_rejects_write_request_retries_before_creating_run():
    from app.modules.collection.execution import _execute
    from app.modules.collection.schemas import HttpSourceInput

    config = HttpSourceInput.model_validate({
        "name": "write source", "url": "https://api.example.test/items", "method": "POST",
        "bodies": [{"item": 1}], "allowAutomatic": True,
    })
    with pytest.raises(AppValidationError, match="不支持自动重试"):
        _execute(None, SimpleNamespace(id="src_1"), config, {}, request_retries=1)


def test_failed_execution_cas_uses_current_locked_control_state():
    """Cancellation conflict recovery must bypass a stale MySQL RR snapshot."""
    from app.modules.workflows import engine

    class NoAutoflush:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Result:
        def one_or_none(self):
            return None

    class FakeDB:
        no_autoflush = NoAutoflush()

        def __init__(self):
            self.statement = None

        def execute(self, statement):
            self.statement = statement
            return Result()

    db = FakeDB()
    assert engine._remote_execution_state(db, "execution-1") is None
    assert db.statement._for_update_arg is not None


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_http_empty_node_overrides_preserve_saved_body_and_pagination(monkeypatch, method):
    from app.modules.collection.schemas import HttpSourceInput
    from app.modules.collection import sources
    from app.modules.workflows import nodes
    from app.modules.workflows.expressions import RuleBudget
    from app.modules.workflows.schemas import HttpConfig

    saved = HttpSourceInput.model_validate({
        "name": "saved", "url": "https://api.example.test/items", "method": method,
        "bodies": [{"query": "saved-body"}] if method == "POST" else [None],
        "pagination": {"parameter": "page", "start": 1, "step": 1, "pages": 3} if method == "GET" else None,
        "allowAutomatic": True,
    })
    accesses, calls = [], []
    def bound_source(_ctx, _id, action, **_):
        accesses.append(action)
        return SimpleNamespace(id="src_1")
    monkeypatch.setattr(sources, "http_source", bound_source)
    monkeypatch.setattr(sources, "source_config", lambda _: saved)
    monkeypatch.setattr(sources, "credentials_for", lambda *_: {})
    def forbidden(*_):
        raise AssertionError("bound-source execution cannot require source-edit approval")
    monkeypatch.setattr(nodes, "require_permission", forbidden)
    def collect(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(success=True, model_dump=lambda **_: {"success": True})
    monkeypatch.setattr(nodes.collection, "collect", collect)
    config = HttpConfig.model_validate({"datasourceId": "src_1", "body": None, "confirmWrites": method != "GET"})
    result = nodes._execute_http(None, config, {}, RuleBudget(time.monotonic() + 5))
    assert result["success"] and accesses == ["datasources.view"]
    assert calls[0]["request_override"]["bodies"] == saved.bodies
    assert calls[0]["request_override"]["pagination"] == saved.pagination
