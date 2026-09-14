"""Creation permission uses current enterprise role, exact template and source policy."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session
from test_office_export import setup

from enterprise_platform.application.errors import AccessDenied, NotFound
from enterprise_platform.persistence.office_creation import RegisteredOfficeCreationAccess


@pytest.mark.parametrize("role", ["owner", "admin", "editor"])
def test_creation_rechecks_template_and_sources_in_callers_transaction(role):
    _, principal, _, _, _, record = setup()
    principal = principal.model_copy(update={"workspace_role": role})
    sources, templates, session = Mock(), Mock(), Mock(spec=Session)
    policy = RegisteredOfficeCreationAccess(sources, templates)
    policy.require(session, principal, record)
    templates.assert_called_once_with(record)
    args = sources.require.call_args.args
    assert args[0] is session and args[2] is record
    assert (args[1].workspace_id, args[1].actor_id, args[1].file_id, args[1].action) == (
        principal.workspace_id,
        principal.actor_id,
        record.content.file_id,
        "read",
    )
    session.commit.assert_not_called()


@pytest.mark.parametrize("reason", ["role", "workspace", "template", "source"])
def test_creation_denies_missing_entitlement_without_committing(reason):
    _, principal, _, _, _, record = setup()
    if reason != "role":
        principal = principal.model_copy(update={"workspace_role": "editor"})
    if reason == "workspace":
        record = replace(record, workspace_id="other")
    sources, templates, session = Mock(), Mock(), Mock(spec=Session)
    if reason == "template":
        templates.side_effect = NotFound()
    if reason == "source":
        sources.require.side_effect = AccessDenied()
    with pytest.raises((AccessDenied, NotFound)):
        RegisteredOfficeCreationAccess(sources, templates).require(session, principal, record)
    if reason != "source":
        sources.require.assert_not_called()
    session.commit.assert_not_called()
