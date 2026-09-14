"""Revocation primitives participate in caller transactions, not HTTP authorization."""

import pytest
from test_office_source_access import database as database
from test_office_source_access import inputs

from enterprise_platform.application.errors import AccessDenied, Conflict
from enterprise_platform.persistence.office_source_access import SqlAlchemyOfficeSourceAccess
from enterprise_platform.persistence.office_source_models import OfficeSourceGrantRow, OfficeSourceRow
from enterprise_platform.persistence.office_source_revocation import revoke_source_reader


def test_revoke_denies_existing_file_grant_and_bumps_source_revision(database):
    assert (
        revoke_source_reader(database, workspace_id="ws", source_id="source", actor_id="actor", expected_revision=1)
        == 2
    )
    database.flush()
    with pytest.raises(AccessDenied):
        SqlAlchemyOfficeSourceAccess().require(database, *inputs())
    assert database.get(OfficeSourceRow, ("ws", "source")).acl_revision == 2


def test_rollback_restores_grant_and_revision(database):
    revoke_source_reader(database, workspace_id="ws", source_id="source", actor_id="actor", expected_revision=1)
    database.flush()
    database.rollback()
    assert database.get(OfficeSourceRow, ("ws", "source")).acl_revision == 1
    SqlAlchemyOfficeSourceAccess().require(database, *inputs())


@pytest.mark.parametrize("revision", [0, True, 2, 1.0])
def test_invalid_or_stale_revision_does_not_revoke(database, revision):
    with pytest.raises(Conflict):
        revoke_source_reader(
            database, workspace_id="ws", source_id="source", actor_id="actor", expected_revision=revision
        )
    assert database.get(OfficeSourceGrantRow, ("ws", "source", "actor")).can_read


def test_wrong_workspace_denied_without_changing_existing_grant(database):
    with pytest.raises(AccessDenied):
        revoke_source_reader(database, workspace_id="other", source_id="source", actor_id="actor", expected_revision=1)
    assert database.get(OfficeSourceGrantRow, ("ws", "source", "actor")).can_read


def test_repeated_revocation_is_noop_at_current_revision(database):
    revoke_source_reader(database, workspace_id="ws", source_id="source", actor_id="actor", expected_revision=1)
    database.flush()
    assert (
        revoke_source_reader(database, workspace_id="ws", source_id="source", actor_id="actor", expected_revision=2)
        == 2
    )


def test_revision_exhaustion_does_not_partially_revoke(database):
    head = database.get(OfficeSourceRow, ("ws", "source"))
    head.acl_revision = 9223372036854775807
    database.flush()
    with pytest.raises(Conflict):
        revoke_source_reader(
            database, workspace_id="ws", source_id="source", actor_id="actor", expected_revision=head.acl_revision
        )
    assert database.get(OfficeSourceGrantRow, ("ws", "source", "actor")).can_read
