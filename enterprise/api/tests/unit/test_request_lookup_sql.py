"""Compile the lookup issued by the repository; database behavior is CI-only."""

from unittest.mock import create_autospec, patch
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import JsonObject, RunSpec
from enterprise_platform.application.errors import Conflict
from enterprise_platform.persistence.models import RunRow
from enterprise_platform.persistence.repository import SqlAlchemyRepository
from enterprise_platform.persistence.runs import verify_recompute


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
def test_lookup_statement_binds_exact_workspace_and_request_key(dialect: object) -> None:
    sessions = create_autospec(sessionmaker, instance=True)
    session = create_autospec(Session, instance=True)
    session.scalar.return_value = None
    with patch("enterprise_platform.persistence.runs.transaction") as transaction:
        transaction.return_value.__enter__.return_value = session
        result = SqlAlchemyRepository(sessions).find_run_by_request_key("workspace-1", "request / 0001%?")
    assert result is None
    statement = session.scalar.call_args.args[0].compile(dialect=dialect)
    assert statement.params == {"workspace_id_1": "workspace-1", "request_key_1": "request / 0001%?"}
    where = str(statement).split("WHERE")[1]
    assert "enterprise_runs.workspace_id =" in where
    assert "enterprise_runs.request_key =" in where
    assert " AND " in where
    session.add.assert_not_called()
    session.execute.assert_not_called()


@pytest.mark.parametrize("parameters", [{"batch": True}, {"batch": 1.0}])
def test_recompute_policy_requires_json_typed_parameter_identity(parameters: JsonObject) -> None:
    spec = RunSpec(
        app_id="app",
        workflow_id=UUID(int=1),
        specification_revision="spec-1",
        secret_ref="secret-ref",
        source_id="source",
        source_revision="source-v1",
        read_id="read",
        read_revision="read-v1",
        binding_id="binding",
        binding_revision=1,
        device_id="device",
        scenario="alert",
        parameters={"batch": 1},
    )
    snapshot = '{"measurement":"0001"}'
    previous = RunRow(spec_json=spec.model_dump_json(), input_json=snapshot)
    changed = spec.model_copy(update={"parameters": parameters})
    with pytest.raises(Conflict, match="recompute_snapshot_conflict"):
        verify_recompute(changed, snapshot, previous)
