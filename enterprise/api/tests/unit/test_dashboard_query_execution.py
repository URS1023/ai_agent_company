import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import create_autospec

import httpx
import pytest
from test_dashboard_query_capture import captured, contract

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.application.contracts import Device, Principal
from enterprise_platform.application.dashboard_query_execution import (
    ApprovedDeviceDashboardRead,
    DeviceDashboardQueryExecutor,
    DeviceDashboardQueryRegistry,
)
from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.application.input_capture import RegisteredRead, RegisteredSourceReader, SourceReaderPort
from enterprise_platform.domain.data_sources import HttpRead, HttpSourceConfig


def actor() -> Principal:
    return Principal(actor_id="actor-1", workspace_id="workspace-1", workspace_role="editor", display_name="Editor")


def plan() -> ApprovedDeviceDashboardRead:
    expected = contract()
    read = HttpRead(
        source=expected.source,
        read_id=expected.read_id,
        revision=expected.read_revision,
        method="GET",
        read_only=True,
        parameter_names=frozenset({"device"}),
        rows_path=("data",),
    )
    entry = RegisteredRead(
        connection=HttpSourceConfig(
            source=expected.source,
            url="https://source.example.test/data",
            allowed_hosts=frozenset({"source.example.test"}),
        ),
        read=read,
        device_ids=frozenset({"device-1"}),
        device_parameter="device",
        device_column="device",
    )
    now = datetime.now(UTC)
    device = Device(
        id="device-1",
        workspace_id="workspace-1",
        device_code="device-1",
        name="Device",
        department="Production",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    params = (("device", "device-1"),)
    return ApprovedDeviceDashboardRead(
        "actor-1", device, entry, replace(expected, read_fingerprint=read_fingerprint(read, params)), params
    )


def setup_executor():
    expected = plan()
    registry = create_autospec(DeviceDashboardQueryRegistry, instance=True)
    registry.resolve.return_value = expected
    reader = create_autospec(SourceReaderPort, instance=True)
    reader.read.return_value = replace(captured(), read_fingerprint=expected.contract.read_fingerprint)
    return DeviceDashboardQueryExecutor(registry, reader), registry, reader, expected


def test_executes_approved_read_and_rechecks_authority_before_returning() -> None:
    executor, registry, reader, expected = setup_executor()
    result = asyncio.run(executor.execute(actor(), expected.contract.binding))
    reader.read.assert_awaited_once_with(expected.registration, {"device": "device-1"})
    assert registry.resolve.call_count == 2
    assert result.rows[0]["device"] == "device-1"


@pytest.mark.parametrize("case", ["actor", "workspace", "device", "parameters", "query_revision"])
def test_mismatched_approval_never_dispatches(case: str) -> None:
    executor, registry, reader, expected = setup_executor()
    if case == "actor":
        expected = replace(expected, actor_id="other")
    elif case == "workspace":
        expected = replace(expected, device=expected.device.model_copy(update={"workspace_id": "other"}))
    elif case == "device":
        expected = replace(expected, device=expected.device.model_copy(update={"id": "other"}))
    elif case == "parameters":
        expected = replace(expected, parameters=(("device", "other"),))
    else:
        expected = replace(
            expected,
            contract=replace(expected.contract, binding=replace(expected.contract.binding, query_revision="other")),
        )
    registry.resolve.return_value = expected
    with pytest.raises((AccessDenied, Conflict)):
        asyncio.run(executor.execute(actor(), contract().binding))
    reader.read.assert_not_awaited()


def test_wrong_device_rows_are_rejected_not_silently_filtered() -> None:
    executor, registry, reader, expected = setup_executor()
    reader.read.return_value = replace(
        reader.read.return_value, rows=(("other-device", reader.read.return_value.rows[0][1]),)
    )
    with pytest.raises(AccessDenied):
        asyncio.run(executor.execute(actor(), expected.contract.binding))


def test_revocation_during_read_prevents_result_delivery() -> None:
    executor, registry, reader, expected = setup_executor()
    registry.resolve.side_effect = [expected, AccessDenied()]
    with pytest.raises(AccessDenied):
        asyncio.run(executor.execute(actor(), expected.contract.binding))
    reader.read.assert_awaited_once()


def test_real_http_reader_composes_with_whole_batch_refresh_service() -> None:
    from decimal import Decimal

    from enterprise_platform.application.dashboard_refresh_service import (
        DashboardRecord,
        DashboardRefreshService,
        DashboardRepository,
    )
    from enterprise_platform.domain import dashboard as d

    expected = plan()
    registry = create_autospec(DeviceDashboardQueryRegistry, instance=True)
    registry.resolve.return_value = expected
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.params["device"])
        return httpx.Response(200, content=b'{"data":[{"device":"device-1","rate":98.2500}]}')

    executor = DeviceDashboardQueryExecutor(
        registry, RegisteredSourceReader(http_transport=httpx.MockTransport(respond))
    )
    design = d.DesignSnapshot(
        "equipment",
        1,
        1,
        '{"style":"fixed"}',
        "lynx",
        "1",
        slots=(
            d.SlotContract(
                slot_id="metric",
                columns=(
                    d.ColumnContract(name="name", kind="string"),
                    d.ColumnContract(name="value", kind="decimal", unit="%"),
                ),
            ),
        ),
    )
    record = DashboardRecord(
        "workspace-1", "dashboard-1", 1, design, (expected.contract.binding,), d.RefreshState(design.identity)
    )
    repository = create_autospec(DashboardRepository, instance=True)
    repository.get.return_value = record
    repository.commit.side_effect = lambda **values: values["state"]
    service = DashboardRefreshService(repository, executor, id_factory=lambda: "batch-1")
    state = asyncio.run(service.refresh(actor(), "dashboard-1", expected_revision=1))
    assert state.status == "ready"
    assert dict(state.current.slots[0].rows[0]) == {"name": "device-1", "value": Decimal("98.2500")}
    assert requests == ["device-1"]
    repository.commit.assert_called_once()


def test_read_only_actor_never_resolves_or_reads_queries() -> None:
    executor, registry, reader, expected = setup_executor()
    with pytest.raises(AccessDenied):
        asyncio.run(
            executor.execute(actor().model_copy(update={"workspace_role": "normal"}), expected.contract.binding)
        )
    registry.resolve.assert_not_called()
    reader.read.assert_not_awaited()
