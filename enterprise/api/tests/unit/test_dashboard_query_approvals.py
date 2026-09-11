import json
from pathlib import Path

import pytest
from test_dashboard_query_registry import setup_registry

from enterprise_platform.adapters.dashboard_query_approvals import FileDashboardQueryApprovals
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable


def write(path: Path, *, enabled: bool = True) -> None:
    _, approvals, _, _, _ = setup_registry()
    approval = approvals.get.return_value.model_copy(update={"enabled": enabled})
    path.write_text(
        json.dumps({"schema_version": 1, "approvals": [approval.model_dump(mode="json")]}), encoding="utf-8"
    )


def test_constructor_does_not_read_or_create_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    store = FileDashboardQueryApprovals(path)
    assert not path.exists()
    with pytest.raises(DependencyUnavailable, match="dashboard_approval_source_unavailable"):
        store.get("workspace-1", "query-1", "query-rev-1")
    with pytest.raises(DependencyUnavailable, match="dashboard_approval_source_unavailable"):
        store.list_for_actor("workspace-1", "actor-1")


def test_reads_exact_scope_and_reloads_revocation(tmp_path: Path) -> None:
    path = tmp_path / "approvals.json"
    write(path)
    store = FileDashboardQueryApprovals(path)
    assert store.get("workspace-1", "query-1", "query-rev-1").enabled
    write(path, enabled=False)
    assert not store.get("workspace-1", "query-1", "query-rev-1").enabled
    for key in [
        ("other", "query-1", "query-rev-1"),
        ("workspace-1", "other", "query-rev-1"),
        ("workspace-1", "query-1", "other"),
    ]:
        with pytest.raises(AccessDenied):
            store.get(*key)


def test_discovery_filters_scope_actor_and_revocation_and_sorts(tmp_path: Path) -> None:
    path = tmp_path / "approvals.json"
    _, approvals, _, _, _ = setup_registry()
    base = approvals.get.return_value
    entries = [
        base.model_copy(update={"query_ref": "z-query"}),
        base,
        base.model_copy(update={"query_ref": "disabled", "enabled": False}),
        base.model_copy(update={"query_ref": "other-actor", "actor_ids": frozenset({"other"})}),
        base.model_copy(update={"workspace_id": "other"}),
        base.model_copy(
            update={"query_ref": "foreign-source", "source": base.source.model_copy(update={"workspace_id": "other"})}
        ),
    ]
    path.write_text(
        json.dumps({"schema_version": 1, "approvals": [item.model_dump(mode="json") for item in entries]}),
        encoding="utf-8",
    )
    store = FileDashboardQueryApprovals(path)

    found = store.list_for_actor("workspace-1", "actor-1")

    assert [item.query_ref for item in found] == ["query-1", "z-query"]
    assert store.list_for_actor("workspace-1", "unapproved") == ()
    write(path, enabled=False)
    assert store.list_for_actor("workspace-1", "actor-1") == ()


def test_discovery_does_not_fall_back_after_authority_corruption(tmp_path: Path) -> None:
    path = tmp_path / "approvals.json"
    write(path)
    store = FileDashboardQueryApprovals(path)
    assert len(store.list_for_actor("workspace-1", "actor-1")) == 1
    path.write_text("{", encoding="utf-8")

    with pytest.raises(DependencyUnavailable, match="dashboard_approval_source_unavailable"):
        store.list_for_actor("workspace-1", "actor-1")


def test_registry_discovery_uses_file_and_catalog_without_querying_source(tmp_path: Path, monkeypatch) -> None:
    import httpx
    from test_dashboard_query_execution import actor

    from enterprise_platform.application.dashboard_query_registry import RegisteredDeviceDashboardQueries
    from enterprise_platform.application.input_capture import ImmutableReadCatalog

    def unexpected_send(*args, **kwargs):
        pytest.fail("Query discovery must not execute HTTP reads")

    monkeypatch.setattr(httpx.AsyncClient, "send", unexpected_send)
    monkeypatch.setattr(httpx.Client, "send", unexpected_send)
    path = tmp_path / "approvals.json"
    write(path)
    _, _, _, devices, expected = setup_registry()
    registry = RegisteredDeviceDashboardQueries(
        FileDashboardQueryApprovals(path), ImmutableReadCatalog((expected.registration,)), devices
    )

    choices = registry.discover(actor())

    assert len(choices) == 1
    assert choices[0].device_id == "device-1"
    assert choices[0].parameters == ()
    write(path, enabled=False)
    assert registry.discover(actor()) == ()


@pytest.mark.parametrize("case", ["duplicate", "bad_json", "too_large", "unknown_field", "wrong_version"])
def test_invalid_file_never_reuses_last_good_authority(tmp_path: Path, case: str) -> None:
    path = tmp_path / "approvals.json"
    write(path)
    store = FileDashboardQueryApprovals(path)
    assert store.get("workspace-1", "query-1", "query-rev-1").enabled
    document = json.loads(path.read_text())
    if case == "duplicate":
        document["approvals"] *= 2
    elif case == "unknown_field":
        document["password"] = "sensitive-fixture"
    elif case == "wrong_version":
        document["schema_version"] = 2
    content = "{" if case == "bad_json" else " " * (1024 * 1024 + 1) if case == "too_large" else json.dumps(document)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(DependencyUnavailable) as caught:
        store.get("workspace-1", "query-1", "query-rev-1")
    assert str(caught.value) == "dashboard_approval_source_unavailable"
    with pytest.raises(DependencyUnavailable) as discovery_error:
        store.list_for_actor("workspace-1", "actor-1")
    assert str(discovery_error.value) == "dashboard_approval_source_unavailable"


@pytest.mark.parametrize("revoke_during_read", [False, True])
def test_file_authority_composes_with_registry_and_http_reader(tmp_path: Path, revoke_during_read: bool) -> None:
    import asyncio
    from decimal import Decimal

    import httpx
    from test_dashboard_query_execution import actor

    from enterprise_platform.application.dashboard_query_execution import DeviceDashboardQueryExecutor
    from enterprise_platform.application.dashboard_query_registry import RegisteredDeviceDashboardQueries
    from enterprise_platform.application.input_capture import ImmutableReadCatalog, RegisteredSourceReader

    path = tmp_path / "approvals.json"
    write(path)
    _, _, _, devices, expected = setup_registry()
    registry = RegisteredDeviceDashboardQueries(
        FileDashboardQueryApprovals(path), ImmutableReadCatalog((expected.registration,)), devices
    )
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["device"])
        if revoke_during_read:
            write(path, enabled=False)
        return httpx.Response(200, content=b'{"data":[{"device":"device-1","rate":98.2500}]}')

    executor = DeviceDashboardQueryExecutor(
        registry, RegisteredSourceReader(http_transport=httpx.MockTransport(respond))
    )
    if revoke_during_read:
        with pytest.raises(AccessDenied):
            asyncio.run(executor.execute(actor(), expected.contract.binding))
    else:
        result = asyncio.run(executor.execute(actor(), expected.contract.binding))
        assert result.rows[0]["rate"] == Decimal("98.2500")
        write(path, enabled=False)
    with pytest.raises(AccessDenied):
        asyncio.run(executor.execute(actor(), expected.contract.binding))
    assert calls == ["device-1"]
