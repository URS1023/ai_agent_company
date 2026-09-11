from datetime import timedelta
from unittest.mock import create_autospec

import pytest
from test_business_service import binding
from test_input_capture import device, run_record
from test_scheduling import START, schedule

from enterprise_platform.application.contracts import Binding
from enterprise_platform.application.errors import InvalidInput
from enterprise_platform.application.input_capture import (
    ParameterDeclaration,
    RegisteredRead,
    RegisteredReadRegistry,
    _bind,
)
from enterprise_platform.application.schedule_read_preflight import validate_schedule_read
from enterprise_platform.application.scheduling import prepare_schedule_run
from enterprise_platform.domain.data_sources import HttpRead, HttpSourceConfig, SourceRef


def registration(bound: Binding) -> RegisteredRead:
    source = SourceRef(workspace_id=bound.workspace_id, source_id=bound.source_id, revision=bound.source_revision)
    return RegisteredRead(
        connection=HttpSourceConfig(
            source=source, url="https://gateway.example.test/data", allowed_hosts=frozenset({"gateway.example.test"})
        ),
        read=HttpRead(
            source=source,
            read_id=bound.read_id,
            revision=bound.read_revision,
            method="GET",
            read_only=True,
            parameter_names=frozenset({"device", "from_time", "until_time"}),
        ),
        device_ids=frozenset({bound.device_id}),
        device_parameter="device",
        device_column="device",
        parameters=(
            ParameterDeclaration(input_key="window_start", parameter_name="from_time", kind="datetime"),
            ParameterDeclaration(input_key="window_end", parameter_name="until_time", kind="datetime"),
        ),
    )


def fixture():
    bound = binding().model_copy(update={"manifest": {"input_keys": ["window_start", "window_end"]}})
    registry = create_autospec(RegisteredReadRegistry, instance=True)
    registry.resolve.return_value = registration(bound)
    return bound, registry


def test_preflight_resolves_exact_read_version_without_reading_source_rows() -> None:
    bound, registry = fixture()
    validate_schedule_read(bound, registry)
    registry.resolve.assert_called_once_with("w", "source-1", "source-v1", "read-1", "read-1")


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "other"),
        ("source_id", "other"),
        ("source_revision", "other"),
        ("read_id", "other"),
        ("read_revision", "other"),
        ("device_id", "other"),
    ],
)
def test_preflight_rejects_mismatched_registration(field, value) -> None:
    bound, registry = fixture()
    with pytest.raises(InvalidInput):
        validate_schedule_read(bound.model_copy(update={field: value}), registry)


@pytest.mark.parametrize("patch", [{"kind": "string"}, {"nullable": True}])
def test_preflight_requires_nonnullable_datetime_window_mapping(patch) -> None:
    bound, registry = fixture()
    old = registry.resolve.return_value
    registry.resolve.return_value = old.model_copy(
        update={"parameters": (old.parameters[0].model_copy(update=patch), old.parameters[1])}
    )
    with pytest.raises(InvalidInput):
        validate_schedule_read(bound, registry)


def test_preflight_rejects_binding_without_declared_window_inputs() -> None:
    _, registry = fixture()
    with pytest.raises(InvalidInput):
        validate_schedule_read(binding(), registry)


def test_preflight_propagates_missing_read_instead_of_using_latest() -> None:
    bound, registry = fixture()
    registry.resolve.side_effect = InvalidInput("registered_read_unavailable")
    with pytest.raises(InvalidInput):
        validate_schedule_read(bound, registry)


def test_prepared_window_reaches_source_binding_as_typed_datetimes_and_server_device_scope() -> None:
    bound, registry = fixture()
    validate_schedule_read(bound, registry)
    spec = prepare_schedule_run(schedule(workspace_id="w", binding_id="b", device_id="d"), bound, START)
    assert spec is not None
    record = run_record().model_copy(update={"workspace_id": "w", "spec": spec})
    selected_device = device().model_copy(update={"workspace_id": "w", "id": "d", "device_code": "0007"})
    scope, parameters = _bind(record, selected_device, registry.resolve.return_value)
    assert scope == "0007"
    assert parameters == {"device": "0007", "from_time": START - timedelta(seconds=300), "until_time": START}
