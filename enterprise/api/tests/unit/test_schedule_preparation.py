from datetime import timedelta

import pytest
from test_business_service import binding
from test_scheduling import START, schedule

from enterprise_platform.application.errors import Conflict, InvalidInput
from enterprise_platform.application.scheduling import prepare_schedule_run


def fixture():
    value = schedule(workspace_id="w", binding_id="b", device_id="d")
    bound = binding().model_copy(update={"manifest": {"input_keys": ["window_start", "window_end", "batch"]}})
    return value, bound


def test_preparation_freezes_binding_and_server_window_without_mutating_parameters() -> None:
    value, bound = fixture()
    parameters = {"batch": "night"}
    spec = prepare_schedule_run(value, bound, START, parameters=parameters)
    assert spec is not None
    assert spec.binding_id == bound.id and spec.source_revision == bound.source_revision
    assert spec.parameters == {
        "batch": "night",
        "window_start": (START - timedelta(seconds=300)).isoformat(),
        "window_end": START.isoformat(),
    }
    assert parameters == {"batch": "night"}
    assert spec.recompute_of is None


@pytest.mark.parametrize(
    "parameters",
    [{"window_start": "override"}, {"window_end": "override"}, {"unknown": 1}, {"enterprise_actor_id": "other"}],
)
def test_preparation_rejects_window_override_and_unregistered_inputs(parameters) -> None:
    value, bound = fixture()
    with pytest.raises(InvalidInput):
        prepare_schedule_run(value, bound, START, parameters=parameters)


@pytest.mark.parametrize(
    "manifest", [{}, {"input_keys": ["batch"]}, {"input_keys": "window_start"}, {"input_keys": ["window_start", 2]}]
)
def test_preparation_requires_declared_window_inputs(manifest) -> None:
    value, bound = fixture()
    with pytest.raises(InvalidInput):
        prepare_schedule_run(value, bound.model_copy(update={"manifest": manifest}), START)


@pytest.mark.parametrize(
    "patch",
    [
        {"workspace_id": "other"},
        {"revision": 3},
        {"id": "replacement"},
        {"device_id": "other"},
        {"scenario": "quality"},
    ],
)
def test_preparation_rejects_stale_or_foreign_bindings(patch) -> None:
    value, bound = fixture()
    with pytest.raises(Conflict):
        prepare_schedule_run(value, bound.model_copy(update=patch), START)


def test_paused_early_and_expired_skip_ticks_produce_no_run() -> None:
    value, bound = fixture()
    assert prepare_schedule_run(value.model_copy(update={"enabled": False}), bound, START) is None
    assert prepare_schedule_run(value, bound, START - timedelta(seconds=1)) is None
    assert prepare_schedule_run(value, bound, START + timedelta(seconds=11)) is None
