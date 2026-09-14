"""Persist actual source rows without precision loss or mutable snapshot replay."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from test_office_source_access import database as database

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.application.input_capture import decode_rows
from enterprise_platform.domain.data_sources import FrozenRows, PageEvidence, SourceRef
from enterprise_platform.persistence.office_snapshot_capture import capture_office_snapshot
from enterprise_platform.persistence.office_source_models import OfficeSnapshotRow, OfficeSourceGrantRow


def actor():
    return Principal(workspace_id="ws", actor_id="actor", workspace_role="normal", display_name="Actor")


def rows():
    return FrozenRows(
        SourceRef(workspace_id="ws", source_id="source", revision="r1"),
        "read",
        "read-r1",
        "a" * 64,
        datetime(2026, 9, 14, tzinfo=UTC),
        ("value",),
        ((Decimal("1.2300000000000000001"),), (9007199254740993,), (None,)),
        (PageEvidence(1, 3, "b" * 64),),
    )


def test_exact_source_rows_and_binding_are_saved(database):
    identifier = UUID(int=50)
    assert capture_office_snapshot(database, actor(), identifier, rows()) == str(identifier)
    stored = database.get(OfficeSnapshotRow, ("ws", str(identifier)))
    assert (stored.source_id, stored.source_revision) == ("source", "r1")
    assert decode_rows(json.loads(stored.payload_json)) == rows()


def test_exact_retry_preserves_existing_snapshot(database):
    identifier = UUID(int=50)
    capture_office_snapshot(database, actor(), identifier, rows())
    before = database.get(OfficeSnapshotRow, ("ws", str(identifier))).payload_json
    capture_office_snapshot(database, actor(), identifier, rows())
    assert database.get(OfficeSnapshotRow, ("ws", str(identifier))).payload_json == before


def test_changed_retry_never_overwrites_history(database):
    identifier = UUID(int=50)
    capture_office_snapshot(database, actor(), identifier, rows())
    with pytest.raises(Conflict):
        capture_office_snapshot(database, actor(), identifier, replace(rows(), read_revision="changed"))
    stored = database.get(OfficeSnapshotRow, ("ws", str(identifier)))
    assert decode_rows(json.loads(stored.payload_json)).read_revision == "read-r1"


def test_revoked_actor_is_denied_even_on_retry(database):
    identifier = UUID(int=50)
    capture_office_snapshot(database, actor(), identifier, rows())
    database.get(OfficeSourceGrantRow, ("ws", "source", "actor")).can_read = False
    database.flush()
    with pytest.raises(AccessDenied):
        capture_office_snapshot(database, actor(), identifier, rows())


def test_workspace_mismatch_is_denied(database):
    with pytest.raises(AccessDenied):
        capture_office_snapshot(database, actor().model_copy(update={"workspace_id": "other"}), UUID(int=50), rows())
    assert database.get(OfficeSnapshotRow, ("ws", str(UUID(int=50)))) is None


def test_caller_rollback_removes_snapshot(database):
    capture_office_snapshot(database, actor(), UUID(int=50), rows())
    database.rollback()
    assert database.get(OfficeSnapshotRow, ("ws", str(UUID(int=50)))) is None


@pytest.mark.parametrize("change", ["disabled", "actor", "source", "hash"])
def test_missing_authority_or_corrupt_retry_never_writes(database, change):
    from enterprise_platform.persistence.office_source_models import OfficeSourceRow

    identifier = UUID(int=50)
    capture_office_snapshot(database, actor(), identifier, rows())
    principal, data = actor(), rows()
    if change == "disabled":
        database.get(OfficeSourceRow, ("ws", "source")).enabled = False
    elif change == "actor":
        principal = principal.model_copy(update={"actor_id": "another"})
    elif change == "source":
        data = replace(data, source=SourceRef(workspace_id="ws", source_id="missing", revision="r1"))
    else:
        database.get(OfficeSnapshotRow, ("ws", str(identifier))).payload_hash = "0" * 64
    database.flush()
    expected = Conflict if change == "hash" else AccessDenied
    with pytest.raises(expected):
        capture_office_snapshot(database, principal, identifier, data)


def test_decimal_scale_change_is_not_an_identical_retry(database):
    identifier = UUID(int=50)
    data = replace(rows(), rows=((Decimal("1.2300"),), (9007199254740993,), (None,)))
    capture_office_snapshot(database, actor(), identifier, data)
    changed = replace(data, rows=((Decimal("1.23"),), (9007199254740993,), (None,)))
    with pytest.raises(Conflict):
        capture_office_snapshot(database, actor(), identifier, changed)


def test_changed_source_revision_cannot_reuse_snapshot_id(database):
    identifier = UUID(int=50)
    capture_office_snapshot(database, actor(), identifier, rows())
    changed = replace(rows(), source=SourceRef(workspace_id="ws", source_id="source", revision="r2"))
    with pytest.raises(Conflict):
        capture_office_snapshot(database, actor(), identifier, changed)
    assert database.get(OfficeSnapshotRow, ("ws", str(identifier))).source_revision == "r1"


def test_invalid_snapshot_identifier_is_domain_error(database):
    from enterprise_platform.application.errors import InvalidInput

    with pytest.raises(InvalidInput, match="office_snapshot_id_invalid"):
        capture_office_snapshot(database, actor(), "untyped-id", rows())


@pytest.mark.parametrize("extra", [0, 1])
def test_actual_serialized_size_boundary(database, extra):
    from enterprise_platform.application.contracts import canonical_json
    from enterprise_platform.application.errors import InvalidInput
    from enterprise_platform.application.input_capture import encode_rows

    base = replace(rows(), rows=(("",), (9007199254740993,), (None,)))
    overhead = len(canonical_json(encode_rows(base)).encode("utf-8"))
    limit = 16 * 1024 * 1024
    data = replace(base, rows=(("x" * (limit - overhead + extra),), (9007199254740993,), (None,)))
    assert len(canonical_json(encode_rows(data)).encode("utf-8")) == limit + extra
    identifier = UUID(int=50)
    if extra:
        with pytest.raises(InvalidInput, match="input_snapshot_size_limit"):
            capture_office_snapshot(database, actor(), identifier, data)
        assert database.get(OfficeSnapshotRow, ("ws", str(identifier))) is None
    else:
        capture_office_snapshot(database, actor(), identifier, data)
        assert len(database.get(OfficeSnapshotRow, ("ws", str(identifier))).payload_json.encode("utf-8")) == limit


def test_same_snapshot_id_cannot_be_rebound_to_another_allowed_source(database):
    from enterprise_platform.persistence.office_source_models import OfficeSourceRow

    identifier = UUID(int=50)
    capture_office_snapshot(database, actor(), identifier, rows())
    original = database.get(OfficeSnapshotRow, ("ws", str(identifier))).payload_json
    database.add(OfficeSourceRow(workspace_id="ws", source_id="second", enabled=True, acl_revision=1))
    database.flush()
    database.add(OfficeSourceGrantRow(workspace_id="ws", source_id="second", actor_id="actor", can_read=True))
    database.flush()
    data = replace(rows(), source=SourceRef(workspace_id="ws", source_id="second", revision="r1"))
    with pytest.raises(Conflict):
        capture_office_snapshot(database, actor(), identifier, data)
    assert database.get(OfficeSnapshotRow, ("ws", str(identifier))).payload_json == original
