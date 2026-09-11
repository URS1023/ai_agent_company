import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import create_autospec, patch
from uuid import UUID

import httpx
import pytest

from enterprise_platform.adapters.data_sources import read_fingerprint
from enterprise_platform.application.contracts import Device, Run, RunSpec
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput, InvalidState
from enterprise_platform.application.input_capture import (
    ImmutableReadCatalog,
    InputCaptureService,
    ParameterDeclaration,
    RegisteredRead,
    RegisteredReadRegistry,
    RegisteredSourceReader,
    SourceReaderPort,
    decode_rows,
    encode_rows,
    restore_capture,
)
from enterprise_platform.application.ports import EnterpriseRepository
from enterprise_platform.domain.data_sources import (
    DatabaseSourceConfig,
    DataSourceError,
    FrozenRows,
    HttpRead,
    HttpSourceConfig,
    PageEvidence,
    SourceRef,
    SqlRead,
)


def source() -> SourceRef:
    return SourceRef(workspace_id="workspace-1", source_id="source-1", revision="source-v1")


def registered() -> RegisteredRead:
    return RegisteredRead(
        connection=HttpSourceConfig(
            source=source(), url="https://gateway.example.test/data", allowed_hosts=frozenset({"gateway.example.test"})
        ),
        read=HttpRead(
            source=source(),
            read_id="read-1",
            revision="read-v1",
            method="GET",
            read_only=True,
            parameter_names=frozenset({"device", "batch"}),
            rows_path=("data",),
        ),
        device_ids=frozenset({"device-1"}),
        device_parameter="device",
        device_column="device",
        parameters=(ParameterDeclaration(input_key="batch_id", parameter_name="batch", kind="string"),),
    )


def run_record() -> Run:
    return Run(
        id="run-1",
        workspace_id="workspace-1",
        actor_id="actor-1",
        request_key="request-1",
        payload_hash="p",
        status="claimed",
        dispatch_nonce="nonce-secret",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        spec=RunSpec(
            binding_id="binding-1",
            binding_revision=1,
            device_id="device-1",
            scenario="alert",
            app_id="app-1",
            workflow_id=UUID(int=1),
            specification_revision="spec-1",
            secret_ref="workflow-secret-ref",
            source_id="source-1",
            source_revision="source-v1",
            read_id="read-1",
            read_revision="read-v1",
            parameters={"batch_id": "batch-0001"},
        ),
    )


def device() -> Device:
    return Device(
        id="device-1",
        workspace_id="workspace-1",
        device_code="0001",
        department="department-A",
        name="Gauge",
        revision=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def rows() -> FrozenRows:
    return FrozenRows(
        source(),
        "read-1",
        "read-v1",
        read_fingerprint(registered().read, (("batch", "batch-0001"), ("device", "0001"))),
        datetime.now(UTC),
        ("device", "value"),
        (("0001", Decimal("0.0000000000000000001")),),
        (PageEvidence(1, 1, "a" * 64),),
    )


def dependencies(run=None, entry=None):
    record = run or run_record()
    repository = create_autospec(EnterpriseRepository, instance=True, spec_set=True)
    repository.get_run.return_value = record
    repository.get_device.return_value = device()
    repository.capture_input.side_effect = lambda ws, rid, nonce, snapshot, *, actor_id: record.model_copy(
        update={"input_snapshot": snapshot}
    )
    reader = create_autospec(SourceReaderPort, instance=True, spec_set=True)
    reader.read.return_value = rows()
    service = InputCaptureService(repository, ImmutableReadCatalog((entry or registered(),)), reader)
    return service, repository, reader


def capture(service, nonce="nonce-secret"):
    return asyncio.run(service.capture("workspace-1", "run-1", nonce))


def test_capture_reads_only_on_explicit_node_call_and_persists_actual_typed_rows() -> None:
    service, repository, reader = dependencies()
    reader.read.assert_not_called()

    result = capture(service)

    assert result.records[0]["value"] == Decimal("0.0000000000000000001")
    assert reader.read.call_args.args[1] == {"device": "0001", "batch": "batch-0001"}
    snapshot = repository.capture_input.call_args.args[3]
    assert snapshot["device_id"] == "device-1"
    assert snapshot["scope_value"] == "0001"
    assert "nonce-secret" not in str(snapshot)
    assert "workflow-secret-ref" not in str(snapshot)
    assert "gateway.example.test" not in str(snapshot)
    assert result.pages == rows().pages


@pytest.mark.parametrize("state", ["queued", "succeeded", "failed", "cancelled"])
def test_capture_requires_in_flight_run(state: str) -> None:
    service, repository, reader = dependencies(run_record().model_copy(update={"status": state}))
    with pytest.raises(InvalidState):
        capture(service)
    reader.read.assert_not_called()
    repository.capture_input.assert_not_called()


@pytest.mark.parametrize("nonce", ["wrong", "", "非预期"])
def test_wrong_nonce_does_not_read_or_persist(nonce: str) -> None:
    service, repository, reader = dependencies()
    with pytest.raises(InvalidState):
        capture(service, nonce)
    reader.read.assert_not_called()
    repository.capture_input.assert_not_called()


def test_catalog_resolves_exact_five_part_key_and_rejects_duplicate_version() -> None:
    entry = registered()
    catalog = ImmutableReadCatalog((entry,))
    assert catalog.resolve("workspace-1", "source-1", "source-v1", "read-1", "read-v1") == entry
    with pytest.raises(InvalidInput):
        catalog.resolve("workspace-1", "source-1", "old", "read-1", "read-v1")
    with pytest.raises(ValueError):
        ImmutableReadCatalog((entry, entry))


def test_device_allowlist_is_enforced_before_source_read() -> None:
    entry = registered().model_copy(update={"device_ids": frozenset({"other-device"})})
    service, repository, reader = dependencies(entry=entry)
    with pytest.raises(AccessDenied):
        capture(service)
    reader.read.assert_not_called()
    repository.capture_input.assert_not_called()


@pytest.mark.parametrize(
    "parameters",
    [{"batch_id": "b", "device": "other"}, {"batch_id": "b", "sql": "SELECT *"}, {}, {"batch_id": {"device": "other"}}],
)
def test_user_parameters_cannot_override_device_or_supply_sql(parameters) -> None:
    run = run_record().model_copy(update={"spec": run_record().spec.model_copy(update={"parameters": parameters})})
    service, repository, reader = dependencies(run)
    with pytest.raises(InvalidInput):
        capture(service)
    reader.read.assert_not_called()


def test_source_output_device_scope_is_checked_not_assumed_from_where_clause() -> None:
    service, repository, reader = dependencies()
    reader.read.return_value = replace(rows(), rows=(("different-device", Decimal("99")),))
    with pytest.raises(AccessDenied):
        capture(service)
    repository.capture_input.assert_not_called()


@pytest.mark.parametrize("changed", ["source_id", "source_revision", "read_id", "read_revision"])
def test_wrong_registered_read_revision_never_dispatches(changed: str) -> None:
    original = run_record()
    run = original.model_copy(update={"spec": original.spec.model_copy(update={changed: "other"})})
    service, repository, reader = dependencies(run)
    with pytest.raises(InvalidInput):
        capture(service)
    reader.read.assert_not_called()


def test_existing_capture_ignores_registry_shutdown_and_changed_device_code() -> None:
    service, repository, reader = dependencies()
    capture(service)
    snapshot = repository.capture_input.call_args.args[3]
    original = run_record().model_copy(update={"input_snapshot": snapshot})
    registry = create_autospec(RegisteredReadRegistry, instance=True, spec_set=True)
    registry.resolve.side_effect = InvalidInput("removed_source")
    repository.get_run.return_value = original
    repository.get_device.return_value = device().model_copy(update={"device_code": "9999"})
    repository.get_device.reset_mock()
    reader.read.reset_mock()

    returned = capture(InputCaptureService(repository, registry, reader))

    assert returned.records[0]["device"] == "0001"
    registry.resolve.assert_not_called()
    repository.get_device.assert_not_called()
    reader.read.assert_not_called()


def test_recompute_uses_frozen_snapshot_without_reading_current_source() -> None:
    service, repository, reader = dependencies()
    capture(service)
    snapshot = repository.capture_input.call_args.args[3]
    base = run_record()
    repository.get_run.return_value = base.model_copy(
        update={"input_snapshot": snapshot, "spec": base.spec.model_copy(update={"recompute_of": "original"})}
    )
    reader.read.reset_mock()
    assert capture(service).records[0]["device"] == "0001"
    reader.read.assert_not_called()


def test_recompute_without_snapshot_never_falls_back_to_reading() -> None:
    base = run_record()
    run = base.model_copy(update={"spec": base.spec.model_copy(update={"recompute_of": "original"})})
    service, repository, reader = dependencies(run)
    with pytest.raises(InvalidState):
        capture(service)
    reader.read.assert_not_called()


@pytest.mark.parametrize("changed", ["device_id", "parameters", "read_revision", "source_revision"])
def test_existing_snapshot_must_match_frozen_run_scope(changed: str) -> None:
    service, repository, reader = dependencies()
    capture(service)
    snapshot = repository.capture_input.call_args.args[3]
    original = run_record()
    value = {"batch_id": "different"} if changed == "parameters" else "different"
    repository.get_run.return_value = original.model_copy(
        update={"input_snapshot": snapshot, "spec": original.spec.model_copy(update={changed: value})}
    )
    reader.read.reset_mock()
    with pytest.raises(InvalidInput):
        capture(service)
    reader.read.assert_not_called()


def test_decimal_date_datetime_zero_and_null_survive_typed_codec() -> None:
    values = (
        "0001",
        Decimal("0.0000000000000000001"),
        0,
        None,
        False,
        date(2026, 9, 8),
        datetime(2026, 9, 8, tzinfo=UTC),
    )
    original = replace(
        rows(), columns=("device", "decimal", "zero", "null", "bool", "date", "datetime"), rows=(values,)
    )
    restored = decode_rows(encode_rows(original))
    assert restored == original
    assert type(restored.rows[0][2]) is int
    assert type(restored.rows[0][4]) is bool


def test_registered_decimal_parameter_rejects_binary_float() -> None:
    entry = registered().model_copy(
        update={"parameters": (ParameterDeclaration(input_key="batch_id", parameter_name="batch", kind="decimal"),)}
    )
    base = run_record()
    valid = base.model_copy(
        update={"spec": base.spec.model_copy(update={"parameters": {"batch_id": "0.000000000000000001"}})}
    )
    service, repository, reader = dependencies(valid, entry)
    reader.read.return_value = replace(
        rows(),
        read_fingerprint=read_fingerprint(entry.read, (("batch", Decimal("0.000000000000000001")), ("device", "0001"))),
    )
    capture(service)
    assert reader.read.call_args.args[1]["batch"] == Decimal("0.000000000000000001")
    invalid = base.model_copy(update={"spec": base.spec.model_copy(update={"parameters": {"batch_id": 0.1}})})
    service, repository, reader = dependencies(invalid, entry)
    with pytest.raises(InvalidInput):
        capture(service)
    reader.read.assert_not_called()


def test_http_adapter_is_actually_called_and_then_captured_without_database() -> None:
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["device"] == "0001"
        assert request.url.params["batch"] == "batch-0001"
        return httpx.Response(200, content=b'{"data":[{"device":"0001","value":0.1234567890123456789}]}')

    service, repository, _ = dependencies()
    actual = RegisteredSourceReader(http_transport=httpx.MockTransport(handler))
    service = InputCaptureService(repository, ImmutableReadCatalog((registered(),)), actual)
    result = capture(service)
    assert result.records[0]["value"] == Decimal("0.1234567890123456789")
    assert len(calls) == 1
    repository.capture_input.assert_called_once()


def test_source_failure_and_persistence_conflict_do_not_return_success() -> None:
    service, repository, reader = dependencies()
    reader.read.side_effect = DataSourceError("timeout")
    with pytest.raises(DataSourceError, match="timeout"):
        capture(service)
    repository.capture_input.assert_not_called()
    reader.read.side_effect = None
    repository.capture_input.side_effect = Conflict("input_snapshot_conflict")
    with pytest.raises(Conflict):
        capture(service)


def test_result_from_previous_parameters_cannot_be_relabelled_as_current_capture() -> None:
    original = run_record()
    changed = original.model_copy(
        update={"spec": original.spec.model_copy(update={"parameters": {"batch_id": "batch-0002"}})}
    )
    service, repository, reader = dependencies(changed)
    with pytest.raises(InvalidInput, match="source_result_execution_mismatch"):
        capture(service)
    repository.capture_input.assert_not_called()


def test_missing_device_column_rejects_source_rows() -> None:
    service, repository, reader = dependencies()
    reader.read.return_value = replace(rows(), columns=("unrelated", "value"))
    with pytest.raises(AccessDenied, match="source_row_device_scope_mismatch"):
        capture(service)
    repository.capture_input.assert_not_called()


def test_public_restore_preserves_empty_capture_without_live_lookups() -> None:
    service, repository, reader = dependencies()
    reader.read.return_value = replace(rows(), rows=(), columns=(), pages=(PageEvidence(1, 0, "a" * 64),))
    result = capture(service)
    snapshot = repository.capture_input.call_args.args[3]
    assert restore_capture(run_record().model_copy(update={"input_snapshot": snapshot})) == result
    assert result.rows == ()


@pytest.mark.parametrize("fails", [False, True])
def test_database_bridge_dispatches_bound_values_in_thread_and_closes_reader_without_connecting(fails: bool) -> None:
    from enterprise_platform.adapters.data_sources import DatabaseSourceReader

    config = DatabaseSourceConfig(
        source=source(),
        dialect="postgresql",
        connection_url="postgresql+psycopg://read_only@db.example.test/source",
        allowed_tables=frozenset({"measurements"}),
        read_only_role=True,
    )
    read = SqlRead(
        source=source(),
        read_id="read-1",
        revision="read-v1",
        sql="SELECT device, value FROM measurements WHERE device = :device AND batch = :batch",
    )
    entry = RegisteredRead.model_validate(
        registered().model_copy(update={"connection": config, "read": read}).model_dump()
    )
    fake_reader = create_autospec(DatabaseSourceReader, instance=True, spec_set=True)
    expected = rows()
    fake_reader.read.return_value = expected
    if fails:
        fake_reader.read.side_effect = DataSourceError("database_read_failed")
    bound = {"device": "0001", "batch": "batch-0001"}
    with patch(
        "enterprise_platform.application.input_capture.DatabaseSourceReader", return_value=fake_reader
    ) as factory:
        if fails:
            with pytest.raises(DataSourceError, match="database_read_failed"):
                asyncio.run(RegisteredSourceReader().read(entry, bound))
        else:
            assert asyncio.run(RegisteredSourceReader().read(entry, bound)) == expected
    factory.assert_called_once_with(config)
    fake_reader.read.assert_called_once_with(read, bound)
    fake_reader.close.assert_called_once()


@pytest.mark.parametrize("change", ["missing", "wrong_count", "out_of_order"])
def test_restore_rejects_inconsistent_complete_page_evidence(change: str) -> None:
    service, repository, _ = dependencies()
    capture(service)
    snapshot = repository.capture_input.call_args.args[3]
    if change == "missing":
        snapshot["data"]["pages"] = []
    elif change == "wrong_count":
        snapshot["data"]["pages"][0]["row_count"] = 999
    else:
        snapshot["data"]["pages"][0]["number"] = 2
    with pytest.raises(InvalidInput, match="input_snapshot_invalid"):
        restore_capture(run_record().model_copy(update={"input_snapshot": snapshot}))
