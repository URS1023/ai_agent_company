from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest
from pydantic import ValidationError

from enterprise_platform.domain import dashboard as d


def design() -> d.DesignSnapshot:
    return d.DesignSnapshot(
        template_id="production",
        template_revision=2,
        design_revision=1,
        visual_json='{"widgets":[{"id":"metric","style":{"color":"blue"}}]}',
        renderer_build_id="lynx-build-1",
        component_schema_version="1",
        asset_digests=(d.ResourceDigest(name="background", sha256="a" * 64),),
        font_digests=(d.ResourceDigest(name="body", sha256="b" * 64),),
        slots=(
            d.SlotContract(slot_id="metric", columns=(d.ColumnContract(name="value", kind="decimal", unit="C"),)),
            d.SlotContract(slot_id="table", columns=(d.ColumnContract(name="id", kind="string"),)),
        ),
    )


def proposals() -> tuple[d.BindingProposal, ...]:
    return (
        d.BindingProposal(slot_id="metric", query_ref="temperature-v1", field_map={"value": "temperature"}),
        d.BindingProposal(slot_id="table", query_ref="devices-v1", field_map={"id": "device_id"}),
    )


def executions() -> tuple[d.ExecutionBinding, ...]:
    return tuple(d.ExecutionBinding.from_proposal(proposal, query_revision="revision-1") for proposal in proposals())


def outcomes(value: Decimal = Decimal("0")) -> tuple[d.SlotSuccess, ...]:
    return (
        d.SlotSuccess(
            slot_id="metric",
            result=d.QueryResult(
                execution_binding=executions()[0],
                columns=(d.QueryColumn(name="temperature", kind="decimal", unit="C"),),
                rows=({"temperature": value},),
            ),
        ),
        d.SlotSuccess(
            slot_id="table",
            result=d.QueryResult(
                execution_binding=executions()[1],
                columns=(d.QueryColumn(name="device_id", kind="string"),),
                rows=({"device_id": "0001"},),
            ),
        ),
    )


def successful_state(snapshot: d.DesignSnapshot) -> d.RefreshState:
    return d.commit_refresh(
        snapshot,
        executions(),
        d.RefreshState(design_identity=snapshot.identity),
        d.RefreshBatch(
            batch_id="batch-1",
            design_identity=snapshot.identity,
            expected_current_batch_id=None,
            bindings=executions(),
            outcomes=outcomes(),
        ),
    )


def test_visual_snapshot_is_deeply_isolated_and_canonically_hashed() -> None:
    snapshot = design()
    before = snapshot.visual_json
    detached = snapshot.visual
    detached["widgets"][0]["style"]["color"] = "red"
    reordered = replace(snapshot, visual_json=' {"widgets": [{"style":{"color":"blue"},"id":"metric"}]} ')

    assert snapshot.visual_json == before
    assert reordered.visual_hash == snapshot.visual_hash
    assert reordered.identity == snapshot.identity
    with pytest.raises(FrozenInstanceError):
        snapshot.design_revision = 99  # type: ignore[misc]
    assert isinstance(snapshot, d.DesignSnapshot)


@pytest.mark.parametrize(
    "field,value",
    [
        ("renderer_build_id", "build-2"),
        ("component_schema_version", "2"),
        ("asset_digests", "asset"),
        ("font_digests", "font"),
    ],
)
def test_renderer_assets_and_fonts_are_part_of_visual_identity(field: str, value: str) -> None:
    snapshot = design()
    replacement = (d.ResourceDigest(name=value, sha256="c" * 64),) if field.endswith("digests") else value
    revised = replace(snapshot, **{field: replacement})
    assert revised.visual_hash != snapshot.visual_hash
    assert revised.identity != snapshot.identity


def test_manual_edit_creates_a_new_revision_and_preserves_original() -> None:
    snapshot = design()
    revised = d.manual_design_revision(snapshot, {"widgets": []}, expected_revision=1)
    assert revised.design_revision == 2
    assert revised.visual == {"widgets": []}
    assert snapshot.visual != revised.visual
    assert snapshot.design_revision == 1
    with pytest.raises(d.DashboardError, match="revision_conflict"):
        d.manual_design_revision(snapshot, {}, expected_revision=2)


@pytest.mark.parametrize("unexpected", ["style", "position", "geometry", "option", "visual", "unknown"])
def test_binding_rejects_visual_and_unknown_fields(unexpected: str) -> None:
    payload = {"slot_id": "metric", "query_ref": "q", "field_map": {"value": "v"}, unexpected: {"x": 10}}
    with pytest.raises(ValidationError):
        d.BindingProposal.model_validate(payload)


def test_binding_aliases_only_expose_data_contract() -> None:
    proposal = d.BindingProposal.model_validate(
        {
            "slotId": "metric",
            "queryRef": "q",
            "fieldMap": {"value": "v"},
            "parameters": {"line": "0001"},
        }
    )
    assert proposal.slot_id == "metric"
    assert proposal.parameters == {"line": "0001"}
    with pytest.raises(ValidationError):
        d.BindingProposal(slot_id="metric", query_ref="q", field_map={"value": {"option": "red"}})


def test_decimal_integer_zero_boolean_null_and_identifiers_remain_distinct() -> None:
    columns = (
        d.ColumnContract(name="decimal", kind="decimal"),
        d.ColumnContract(name="zero", kind="integer"),
        d.ColumnContract(name="flag", kind="boolean"),
        d.ColumnContract(name="id", kind="string"),
        d.ColumnContract(name="missing", kind="decimal", nullable=True),
    )
    snapshot = replace(design(), slots=(d.SlotContract(slot_id="values", columns=columns),))
    proposal = d.BindingProposal(
        slot_id="values", query_ref="q", field_map={column.name: column.name for column in columns}
    )
    original = {"decimal": Decimal("0.000000000000000001"), "zero": 0, "flag": False, "id": "0001", "missing": None}
    result = d.QueryResult(
        execution_binding=d.ExecutionBinding.from_proposal(proposal, query_revision="revision-1"),
        columns=tuple(d.QueryColumn(name=c.name, kind=c.kind) for c in columns),
        rows=(original,),
    )

    data = d.validate_slot_result(snapshot, result.execution_binding, result)
    row = dict(data.rows[0])
    original["id"] = "changed"
    assert row == {"decimal": Decimal("0.000000000000000001"), "zero": 0, "flag": False, "id": "0001", "missing": None}
    assert type(row["decimal"]) is Decimal
    assert type(row["zero"]) is int
    assert type(row["flag"]) is bool
    assert row["missing"] is None


@pytest.mark.parametrize("value", [False, "0", 0.1, Decimal("NaN"), Decimal("Infinity"), None])
def test_decimal_columns_reject_coercion_nonfinite_and_disallowed_null(value: d.InputValue) -> None:
    result = d.QueryResult(
        execution_binding=executions()[0], columns=outcomes()[0].result.columns, rows=({"temperature": value},)
    )
    with pytest.raises(d.DashboardError):
        d.validate_slot_result(design(), executions()[0], result)


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "missing_field"),
        ("unit", "unit_mismatch"),
        ("type", "column_type_mismatch"),
        ("limit", "row_limit_exceeded"),
        ("query", "query_mismatch"),
        ("mapping", "unknown_field"),
    ],
)
def test_invalid_source_contracts_have_typed_errors(change: str, code: str) -> None:
    snapshot, proposal, result = design(), proposals()[0], outcomes()[0].result
    if change == "missing":
        result = replace(result, rows=({},))
    elif change == "unit":
        result = replace(result, columns=(d.QueryColumn(name="temperature", kind="decimal", unit="F"),))
    elif change == "type":
        result = replace(result, columns=(d.QueryColumn(name="temperature", kind="string", unit="C"),))
    elif change == "limit":
        snapshot = replace(snapshot, slots=(snapshot.slots[0].model_copy(update={"row_limit": 1}),))
        result = replace(result, rows=result.rows * 2)
    elif change == "query":
        other = d.ExecutionBinding.from_proposal(proposal.model_copy(update={"query_ref": "other"}), query_revision="1")
        result = replace(result, execution_binding=other)
    else:
        proposal = d.BindingProposal(slot_id="metric", query_ref="temperature-v1", field_map={"style": "temperature"})
    with pytest.raises(d.DashboardError) as error:
        d.validate_slot_result(
            snapshot, d.ExecutionBinding.from_proposal(proposal, query_revision="revision-1"), result
        )
    assert error.value.code == code


def test_refresh_commits_all_configured_slots_without_changing_design() -> None:
    snapshot = design()
    identity, visual = snapshot.identity, snapshot.visual_json
    state = successful_state(snapshot)
    assert state.status == "ready"
    assert state.current.batch_id == "batch-1"
    assert {slot.slot_id for slot in state.current.slots} == {"metric", "table"}
    assert snapshot.identity == identity
    assert snapshot.visual_json == visual


@pytest.mark.parametrize("failure", ["source", "missing", "invalid", "duplicate", "unexpected"])
def test_failed_refresh_retains_previous_whole_screen(failure: str) -> None:
    snapshot = design()
    previous = successful_state(snapshot)
    items = outcomes(Decimal("99"))
    if failure == "source":
        items = (items[0], d.SlotFailure(slot_id="table", code="source_timeout"))
    elif failure == "missing":
        items = items[:1]
    elif failure == "invalid":
        items = (replace(items[0], result=replace(items[0].result, rows=({},))), items[1])
    elif failure == "duplicate":
        items = (items[0], items[0], items[1])
    else:
        items = (*items, d.SlotFailure(slot_id="other", code="source_timeout"))
    state = d.commit_refresh(
        snapshot,
        executions(),
        previous,
        d.RefreshBatch(
            batch_id="batch-2",
            design_identity=snapshot.identity,
            expected_current_batch_id="batch-1",
            bindings=executions(),
            outcomes=items,
        ),
    )
    assert state.status == "failed"
    assert state.current is previous.current
    assert state.last_attempt_id == "batch-2"
    assert state.failures
    assert dict(state.current.slots[0].rows[0])["value"] == Decimal("0")
    assert previous.status == "ready"


def test_failed_first_refresh_has_no_fabricated_data() -> None:
    snapshot = design()
    state = d.commit_refresh(
        snapshot,
        executions(),
        d.RefreshState(design_identity=snapshot.identity),
        d.RefreshBatch(
            batch_id="first",
            design_identity=snapshot.identity,
            expected_current_batch_id=None,
            bindings=executions(),
            outcomes=(d.SlotFailure(slot_id="metric", code="source_timeout"),),
        ),
    )
    assert state.status == "failed"
    assert state.current is None
    assert state.is_empty


def test_zero_rows_is_a_successful_real_empty_result() -> None:
    snapshot = design()
    items = tuple(replace(item, result=replace(item.result, rows=())) for item in outcomes())
    state = d.commit_refresh(
        snapshot,
        executions(),
        d.RefreshState(design_identity=snapshot.identity),
        d.RefreshBatch(
            batch_id="empty-data",
            design_identity=snapshot.identity,
            expected_current_batch_id=None,
            bindings=executions(),
            outcomes=items,
        ),
    )
    assert state.status == "ready"
    assert all(slot.rows == () for slot in state.current.slots)


@pytest.mark.parametrize("conflict", ["design", "previous", "state"])
def test_refresh_rejects_stale_design_or_previous_batch(conflict: str) -> None:
    snapshot, previous = design(), successful_state(design())
    batch = d.RefreshBatch(
        batch_id="next",
        design_identity=snapshot.identity,
        expected_current_batch_id="batch-1",
        bindings=executions(),
        outcomes=outcomes(),
    )
    if conflict == "design":
        batch = replace(batch, design_identity="old-design")
    elif conflict == "previous":
        batch = replace(batch, expected_current_batch_id=None)
    else:
        previous = replace(previous, design_identity="another-design")
    with pytest.raises(d.DashboardError, match="revision_conflict"):
        d.commit_refresh(snapshot, executions(), previous, batch)


def test_required_unbound_slot_fails_but_optional_unbound_slot_is_allowed() -> None:
    snapshot = design()
    batch = d.RefreshBatch(
        batch_id="one",
        design_identity=snapshot.identity,
        expected_current_batch_id=None,
        bindings=executions()[:1],
        outcomes=outcomes()[:1],
    )
    failed = d.commit_refresh(snapshot, executions()[:1], d.RefreshState(design_identity=snapshot.identity), batch)
    assert failed.status == "failed"
    optional = replace(snapshot, slots=(snapshot.slots[0], snapshot.slots[1].model_copy(update={"required": False})))
    batch = replace(batch, design_identity=optional.identity)
    state = d.commit_refresh(optional, executions()[:1], d.RefreshState(design_identity=optional.identity), batch)
    assert state.status == "ready"


def test_duplicate_slots_invalid_visuals_and_invalid_digests_are_rejected() -> None:
    snapshot = design()
    with pytest.raises(d.DashboardError):
        replace(snapshot, slots=(snapshot.slots[0], snapshot.slots[0]))
    with pytest.raises(d.DashboardError):
        replace(snapshot, visual_json='{"value": NaN}')
    with pytest.raises(ValidationError):
        d.ResourceDigest(name="font", sha256="not-a-digest")


def test_truncated_result_fails_the_whole_refresh() -> None:
    snapshot = design()
    previous = successful_state(snapshot)
    items = outcomes()
    partial = replace(items[0], result=replace(items[0].result, truncated=True))
    state = d.commit_refresh(
        snapshot,
        executions(),
        previous,
        d.RefreshBatch(
            batch_id="partial",
            design_identity=snapshot.identity,
            expected_current_batch_id="batch-1",
            bindings=executions(),
            outcomes=(partial, items[1]),
        ),
    )
    assert state.status == "failed"
    assert state.current is previous.current
    assert state.failures[0].code == "truncated_result"


def test_late_result_from_previous_parameters_is_not_relabelled_as_current() -> None:
    snapshot = design()
    previous = successful_state(snapshot)
    late_result = outcomes()[0].result
    current_proposal = proposals()[0].model_copy(update={"parameters": {"department": "B"}})
    current_execution = d.ExecutionBinding.from_proposal(current_proposal, query_revision="revision-1")

    with pytest.raises(d.DashboardError, match="binding_conflict"):
        d.validate_slot_result(snapshot, current_execution, late_result)
    assert previous.current.batch_id == "batch-1"


@pytest.mark.parametrize("change", ["parameters", "field_map", "query_revision"])
def test_whole_refresh_rejects_obsolete_execution_binding(change: str) -> None:
    snapshot, previous = design(), successful_state(design())
    expected = executions()
    proposal, revision = proposals()[0], "revision-1"
    if change == "query_revision":
        revision = "revision-2"
    elif change == "parameters":
        proposal = proposal.model_copy(update={"parameters": {"department": "B"}})
    else:
        proposal = proposal.model_copy(update={"field_map": {"value": "backup_temperature"}})
    current = (d.ExecutionBinding.from_proposal(proposal, query_revision=revision), expected[1])
    batch = d.RefreshBatch(
        batch_id="late",
        design_identity=snapshot.identity,
        expected_current_batch_id="batch-1",
        bindings=expected,
        outcomes=outcomes(),
    )

    with pytest.raises(d.DashboardError, match="binding_conflict"):
        d.commit_refresh(snapshot, current, previous, batch)
    assert previous.current.batch_id == "batch-1"


def test_relabelled_batch_still_rejects_stale_slot_and_preserves_screen() -> None:
    snapshot, previous = design(), successful_state(design())
    proposal = proposals()[0].model_copy(update={"parameters": {"department": "B"}})
    current = (d.ExecutionBinding.from_proposal(proposal, query_revision="revision-1"), executions()[1])
    batch = d.RefreshBatch(
        batch_id="late",
        design_identity=snapshot.identity,
        expected_current_batch_id="batch-1",
        bindings=current,
        outcomes=outcomes(),
    )

    state = d.commit_refresh(snapshot, current, previous, batch)

    assert state.status == "failed"
    assert state.current is previous.current
    assert state.failures == (d.SlotFailure("metric", "binding_conflict"),)


def test_execution_binding_is_deeply_frozen_and_committed_with_real_provenance() -> None:
    proposal = proposals()[0].model_copy(update={"parameters": {"filter": {"department": "A"}}})
    execution = d.ExecutionBinding.from_proposal(proposal, query_revision="revision-1")
    before = execution.identity
    proposal.parameters["filter"] = {"department": "B"}
    execution.proposal.parameters["filter"] = {"department": "C"}
    execution.proposal.field_map["value"] = "other"

    assert execution.identity == before
    assert execution.proposal.parameters == {"filter": {"department": "A"}}
    assert execution.proposal.field_map == {"value": "temperature"}
    state = successful_state(design())
    assert state.current.slots[0].binding_hash == executions()[0].identity
    assert state.current.bindings_hash == d.binding_set_identity(executions())
    assert d.binding_set_identity(tuple(reversed(executions()))) == state.current.bindings_hash
