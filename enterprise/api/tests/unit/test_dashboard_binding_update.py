from dataclasses import replace

import pytest
from test_dashboard_refresh_service import record, result

from enterprise_platform.application.dashboard_binding_update import replace_dashboard_bindings
from enterprise_platform.application.errors import InvalidInput
from enterprise_platform.domain import dashboard as d


def ready_record():
    original = record()
    state = d.commit_refresh(
        original.design,
        original.bindings,
        original.state,
        d.RefreshBatch(
            "batch-1",
            original.design.identity,
            None,
            original.bindings,
            tuple(d.SlotSuccess(binding.slot_id, result(binding)) for binding in original.bindings),
        ),
    )
    return replace(original, state=state, name="设备指标", creation_request_hash="a" * 64)


def test_changed_bindings_clear_old_data_and_preserve_design_and_creation_metadata() -> None:
    old = ready_record()
    binding = d.ExecutionBinding.from_proposal(old.bindings[0].proposal, query_revision="v2")
    updated = replace_dashboard_bindings(old, (binding, old.bindings[1]))
    assert updated.revision == old.revision + 1
    assert updated.state == d.RefreshState(old.design.identity)
    assert updated.design == old.design
    assert updated.name == old.name and updated.creation_request_hash == old.creation_request_hash
    assert old.state.status == "ready"


def test_equivalent_reordered_bindings_keep_revision_and_last_good_data() -> None:
    old = ready_record()
    assert replace_dashboard_bindings(old, tuple(reversed(old.bindings))) == old


def test_partial_binding_draft_and_unbinding_are_persistable_but_clear_results() -> None:
    old = ready_record()
    updated = replace_dashboard_bindings(old, old.bindings[:1])
    assert len(updated.bindings) == 1 and updated.state.current is None
    assert replace_dashboard_bindings(old, ()).bindings == ()


@pytest.mark.parametrize(
    "proposal",
    [
        d.BindingProposal(slot_id="unknown", query_ref="query", field_map={"value": "count"}),
        d.BindingProposal(slot_id="a", query_ref="query", field_map={"unknown": "count"}),
    ],
)
def test_unknown_slots_or_fields_are_rejected(proposal: d.BindingProposal) -> None:
    with pytest.raises(InvalidInput):
        replace_dashboard_bindings(ready_record(), (d.ExecutionBinding.from_proposal(proposal, query_revision="v1"),))


def test_duplicate_slots_are_rejected() -> None:
    old = ready_record()
    with pytest.raises(InvalidInput):
        replace_dashboard_bindings(old, (old.bindings[0], old.bindings[0]))
