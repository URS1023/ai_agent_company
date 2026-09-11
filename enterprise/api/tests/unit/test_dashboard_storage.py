from dataclasses import replace
from decimal import Decimal

import pytest

from enterprise_platform.application.dashboard_refresh_service import DashboardRecord
from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.domain import dashboard as d
from enterprise_platform.persistence.dashboard_documents import decode_dashboard, encode_dashboard


def stored_record() -> DashboardRecord:
    design = d.DesignSnapshot(
        template_id="equipment",
        template_revision=1,
        design_revision=1,
        visual_json='{"color":"blue"}',
        renderer_build_id="lynx",
        component_schema_version="1",
        slots=(
            d.SlotContract(
                slot_id="metric",
                columns=(
                    d.ColumnContract(name="decimal", kind="decimal"),
                    d.ColumnContract(name="integer", kind="integer"),
                    d.ColumnContract(name="string", kind="string"),
                    d.ColumnContract(name="boolean", kind="boolean"),
                    d.ColumnContract(name="null", kind="string", nullable=True),
                ),
            ),
        ),
    )
    binding = d.ExecutionBinding.from_proposal(
        d.BindingProposal(
            slot_id="metric",
            query_ref="query",
            field_map={name: name for name in ("decimal", "integer", "string", "boolean", "null")},
            parameters={"window": "2026-09-11"},
        ),
        query_revision="query-v1",
    )
    slot = d.SlotData(
        "metric",
        "query",
        binding.identity,
        (
            (
                ("decimal", Decimal("98.2500")),
                ("integer", 9007199254740993),
                ("string", "98.2500"),
                ("boolean", True),
                ("null", None),
            ),
        ),
    )
    state = d.RefreshState(
        design.identity,
        "ready",
        d.CommittedBatch("batch-1", design.identity, d.binding_set_identity((binding,)), (slot,)),
        "batch-1",
    )
    return DashboardRecord("workspace-1", "dashboard-1", 2, design, (binding,), state)


def test_storage_roundtrip_keeps_scalar_types_decimal_scale_and_frozen_visuals() -> None:
    original = stored_record()
    restored = decode_dashboard(encode_dashboard(original))
    assert restored == original
    value = dict(restored.state.current.slots[0].rows[0])["decimal"]
    assert isinstance(value, Decimal)
    assert value.as_tuple().exponent == -4
    assert restored.design.identity == original.design.identity


def test_dashboard_name_and_creation_fingerprint_roundtrip_without_changing_design() -> None:
    from dataclasses import replace

    original = stored_record()
    named = replace(original, name="一车间设备预警", creation_request_hash="a" * 64)
    restored = decode_dashboard(encode_dashboard(named))
    assert restored == named
    assert restored.design.identity == original.design.identity


def test_legacy_dashboard_documents_default_missing_name_metadata() -> None:
    import json

    document = json.loads(encode_dashboard(stored_record()))
    document.pop("name", None)
    document.pop("creation_request_hash", None)
    restored = decode_dashboard(json.dumps(document))
    assert restored.name == ""
    assert restored.creation_request_hash == ""


def test_failed_refresh_restores_last_good_batch_and_failure_receipt() -> None:
    original = stored_record()
    failed = replace(
        original,
        state=replace(
            original.state,
            status="failed",
            last_attempt_id="batch-2",
            failures=(d.SlotFailure("metric", "source_query_failed"),),
        ),
    )
    assert decode_dashboard(encode_dashboard(failed)) == failed


def test_empty_dashboard_roundtrip() -> None:
    original = stored_record()
    empty = replace(original, state=d.RefreshState(original.design.identity))
    assert decode_dashboard(encode_dashboard(empty)) == empty


@pytest.mark.parametrize(
    "old,new",
    [
        ('"kind":"decimal"', '"kind":"unknown"'),
        ('"value":"98.2500"', '"value":"NaN"'),
        ('"schema_version":1', '"schema_version":2'),
        ('"revision":2', '"revision":0'),
    ],
)
def test_corrupt_documents_return_only_opaque_persistence_error(old: str, new: str) -> None:
    document = encode_dashboard(stored_record())
    assert old in document
    with pytest.raises(PersistenceError, match="dashboard_document_invalid"):
        decode_dashboard(document.replace(old, new, 1))


def test_wrong_state_identity_is_rejected() -> None:
    original = stored_record()
    with pytest.raises(PersistenceError):
        encode_dashboard(replace(original, state=replace(original.state, design_identity="other")))


@pytest.mark.parametrize(
    "case",
    [
        "missing_slot",
        "unknown_slot",
        "wrong_type",
        "extra_field",
        "missing_field",
        "binding_hash",
        "query_ref",
        "batch_hash",
        "attempt",
    ],
)
def test_invalid_committed_data_is_rejected_before_storage(case: str) -> None:
    original = stored_record()
    current = original.state.current
    slot = current.slots[0]
    if case == "missing_slot":
        current = replace(current, slots=())
    elif case == "batch_hash":
        current = replace(current, bindings_hash="c" * 64)
    elif case == "attempt":
        current = replace(current, batch_id="wrong")
    else:
        if case == "unknown_slot":
            slot = replace(slot, slot_id="unknown")
        elif case == "wrong_type":
            slot = replace(
                slot, rows=(tuple((name, "98.25" if name == "decimal" else value) for name, value in slot.rows[0]),)
            )
        elif case == "extra_field":
            slot = replace(slot, rows=(slot.rows[0] + (("style", "red"),),))
        elif case == "missing_field":
            slot = replace(slot, rows=(slot.rows[0][1:],))
        elif case == "binding_hash":
            slot = replace(slot, binding_hash="c" * 64)
        else:
            slot = replace(slot, query_ref="wrong")
        current = replace(current, slots=(slot,))
    with pytest.raises(PersistenceError, match="dashboard_document_invalid"):
        encode_dashboard(replace(original, state=replace(original.state, current=current)))


def test_failed_refresh_can_keep_previous_query_revision_but_not_invalid_rows() -> None:
    original = stored_record()
    failed = replace(
        original,
        bindings=(replace(original.bindings[0], query_revision="query-v2"),),
        state=replace(
            original.state,
            status="failed",
            last_attempt_id="batch-2",
            failures=(d.SlotFailure("metric", "source_query_failed"),),
        ),
    )
    assert decode_dashboard(encode_dashboard(failed)) == failed
    slot = failed.state.current.slots[0]
    bad = replace(failed.state.current, slots=(replace(slot, rows=((("decimal", True),),)),))
    with pytest.raises(PersistenceError):
        encode_dashboard(replace(failed, state=replace(failed.state, current=bad)))


def test_semantic_validation_also_runs_when_loading_existing_documents() -> None:
    document = encode_dashboard(stored_record())
    original = '"kind":"integer","value":"9007199254740993"'
    assert original in document
    changed = document.replace(original, '"kind":"boolean","value":true')
    with pytest.raises(PersistenceError):
        decode_dashboard(changed)


@pytest.mark.parametrize("case", ["duplicate_slot", "duplicate_column", "too_many_rows"])
def test_duplicate_or_oversized_committed_data_is_rejected(case: str) -> None:
    original = stored_record()
    current = original.state.current
    slot = current.slots[0]
    if case == "duplicate_slot":
        current = replace(current, slots=(slot, slot))
    else:
        rows = slot.rows * 1001 if case == "too_many_rows" else (slot.rows[0] + (slot.rows[0][0],),)
        current = replace(current, slots=(replace(slot, rows=rows),))
    with pytest.raises(PersistenceError):
        encode_dashboard(replace(original, state=replace(original.state, current=current)))
