from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from enterprise_platform.application.errors import Conflict, InvalidInput
from enterprise_platform.persistence import migrate_workflow_credentials as migration
from enterprise_platform.persistence.migrate_workflow_setups import require_setup_prerequisites


def inspector():
    metadata = migration.prerequisite_metadata()
    result = Mock()
    result.get_schema_names.return_value = ["public"]
    result.get_table_names.return_value = list(metadata.tables)
    result.get_view_names.return_value = []
    result.get_materialized_view_names.return_value = []
    result.get_columns.side_effect = lambda name, **kw: [
        dict(
            name=c.name,
            type=c.type,
            nullable=c.nullable,
            default=f"nextval('{name}_sequence_seq'::regclass)" if c.name == "sequence" else None,
        )
        for c in metadata.tables[name].c
    ]
    result.get_pk_constraint.side_effect = lambda name, **kw: dict(
        constrained_columns=list(metadata.tables[name].primary_key.columns.keys())
    )
    result.get_foreign_keys.side_effect = lambda name, **kw: [
        dict(
            name=f.name,
            constrained_columns=f.column_keys,
            referred_schema="public",
            referred_table=f.referred_table.name,
            referred_columns=[e.column.name for e in f.elements],
        )
        for f in metadata.tables[name].foreign_key_constraints
    ]
    result.get_unique_constraints.side_effect = lambda name, **kw: [
        dict(name=c.name, column_names=list(c.columns.keys()))
        for c in metadata.tables[name].constraints
        if isinstance(c, UniqueConstraint)
    ]
    result.get_indexes.side_effect = lambda name, **kw: [
        dict(name=i.name, column_names=list(i.columns.keys()), unique=bool(i.unique))
        for i in metadata.tables[name].indexes
    ]
    result.get_check_constraints.side_effect = lambda name, **kw: [
        dict(name=c.name, sqltext=str(c.sqltext))
        for c in metadata.tables[name].constraints
        if isinstance(c, CheckConstraint)
    ]
    return result


def test_requires_exactly_verified_seven_tables_and_old_wrapper_still_requires_six():
    assert len(migration.prerequisite_metadata().tables) == 7
    migration.require_credential_prerequisites(inspector())
    with pytest.raises(Conflict):
        require_setup_prerequisites(inspector())


@pytest.mark.parametrize("change", ["missing_source", "existing_credential", "extra", "source_constraint"])
def test_prerequisite_drift_rejected(change):
    fake = inspector()
    if change == "missing_source":
        fake.get_table_names.return_value.remove("enterprise_source_versions")
    elif change == "source_constraint":
        original = fake.get_check_constraints.side_effect
        fake.get_check_constraints.side_effect = lambda name, **kw: (
            [] if name == "enterprise_source_versions" else original(name, **kw)
        )
    else:
        fake.get_table_names.return_value.append(
            "enterprise_workflow_credentials" if change == "existing_credential" else "apps"
        )
    with pytest.raises(Conflict):
        migration.require_credential_prerequisites(fake)


def test_artifact_stable_explicit_and_print_has_no_engine(monkeypatch, capsys):
    sql = migration.read_workflow_credentials_sql()
    assert sql == migration.render_workflow_credentials_sql() == migration.render_workflow_credentials_sql()
    assert "CREATE TABLE public.enterprise_workflow_credentials" in sql
    assert "CREATE INDEX ix_workflow_credential_active ON public.enterprise_workflow_credentials" in sql
    assert "BEGIN;\nSET LOCAL search_path TO public;" in sql and sql.endswith("COMMIT;\n")
    assert "IF NOT EXISTS" not in sql and "DROP " not in sql
    monkeypatch.setattr(migration, "create_engine", Mock(side_effect=AssertionError("no DB")))
    assert migration.main(["--print-sql"]) == 0
    assert capsys.readouterr().out == sql


def test_wrong_target_before_engine(monkeypatch):
    engine = Mock(side_effect=AssertionError("no DB"))
    monkeypatch.setattr(migration, "create_engine", engine)
    with pytest.raises(InvalidInput):
        migration.apply_workflow_credentials("postgresql+psycopg://user:secret@host/dify", "enterprise_test")
    engine.assert_not_called()


def test_actual_database_mismatch_before_ddl(monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "other"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    with pytest.raises(InvalidInput):
        migration.apply_workflow_credentials("postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test")
    connection.exec_driver_sql.assert_not_called()
    engine.dispose.assert_called_once()


def test_apply_is_locked_and_transactional_after_schema_guard(monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=inspector()))
    migration.apply_workflow_credentials("postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test")
    assert "1162761296" in str(connection.execute.call_args.args[0])
    sql = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert sql[:3] == [
        "SET LOCAL lock_timeout = '10s'",
        "SET LOCAL statement_timeout = '30s'",
        "SET LOCAL search_path TO public",
    ]
    assert sql[3:] == list(migration.workflow_credential_statements())


def test_mismatched_bundle_rejected(monkeypatch):
    package = Mock()
    package.joinpath.return_value.read_text.return_value = "changed artifact"
    monkeypatch.setattr(migration, "files", Mock(return_value=package))
    with pytest.raises(InvalidInput, match="migration_artifact_mismatch"):
        migration.read_workflow_credentials_sql()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--apply"],
        ["--apply", "--url-env", "DATABASE_URL", "--expected-database", "enterprise_test"],
        ["--private-token"],
    ],
)
def test_cli_requires_explicit_enterprise_target_and_hides_arguments(arguments, capsys):
    assert migration.main(arguments) == 1
    captured = capsys.readouterr()
    assert captured.err == "invalid_input\n" and not captured.out


@pytest.mark.parametrize("field", ["columns", "pk_constraint", "indexes", "check_constraints"])
def test_setup_shape_drift_rejected(field):
    fake = inspector()
    method = getattr(fake, f"get_{field}")
    original = method.side_effect
    method.side_effect = lambda name, **kw: (
        ({"constrained_columns": []} if field == "pk_constraint" else [])
        if name == "enterprise_workflow_setups"
        else original(name, **kw)
    )
    with pytest.raises(Conflict):
        migration.require_credential_prerequisites(fake)


def test_missing_setup_table_rejected():
    fake = inspector()
    fake.get_table_names.return_value.remove("enterprise_workflow_setups")
    with pytest.raises(Conflict):
        migration.require_credential_prerequisites(fake)


def test_schema_guard_fails_before_any_credential_ddl(monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    fake = inspector()
    fake.get_table_names.return_value.append("enterprise_workflow_credentials")
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=fake))
    with pytest.raises(Conflict):
        migration.apply_workflow_credentials("postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test")
    assert len(connection.exec_driver_sql.call_args_list) == 3
    engine.begin.return_value.__exit__.assert_called_once()
    engine.dispose.assert_called_once()


def test_database_failure_hides_credentials_and_disposes_engine(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    from enterprise_platform.application.errors import PersistenceError

    engine = MagicMock()
    engine.begin.return_value.__enter__.side_effect = SQLAlchemyError("password=do-not-expose")
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    with pytest.raises(PersistenceError) as raised:
        migration.apply_workflow_credentials("postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test")
    assert "do-not-expose" not in str(raised.value)
    assert raised.value.__suppress_context__
    engine.dispose.assert_called_once()


def test_cli_apply_reads_only_explicit_environment_and_reports_migration(monkeypatch, capsys):
    apply = Mock()
    monkeypatch.setattr(migration, "apply_workflow_credentials", apply)
    monkeypatch.setenv("ENTERPRISE_VAULT_DATABASE_URL", "postgresql+psycopg://reader:secret@host/enterprise_vault")
    assert (
        migration.main(
            ["--apply", "--url-env", "ENTERPRISE_VAULT_DATABASE_URL", "--expected-database", "enterprise_vault"]
        )
        == 0
    )
    apply.assert_called_once_with("postgresql+psycopg://reader:secret@host/enterprise_vault", "enterprise_vault")
    assert capsys.readouterr().out == "Applied enterprise migration 0004.\n"


def test_cli_missing_environment_does_not_connect(monkeypatch, capsys):
    monkeypatch.delenv("ENTERPRISE_VAULT_DATABASE_URL", raising=False)
    engine = Mock(side_effect=AssertionError("no DB"))
    monkeypatch.setattr(migration, "create_engine", engine)
    assert (
        migration.main(
            ["--apply", "--url-env", "ENTERPRISE_VAULT_DATABASE_URL", "--expected-database", "enterprise_vault"]
        )
        == 1
    )
    engine.assert_not_called()
    assert capsys.readouterr().err == "invalid_input\n"


def test_credential_ddl_does_not_modify_prerequisite_metadata():
    from enterprise_platform.persistence.models import Base
    from enterprise_platform.persistence.source_models import SourceBase
    from enterprise_platform.persistence.workflow_credential_models import CredentialBase
    from enterprise_platform.persistence.workflow_setup_models import SetupBase

    before = {table.name: (len(table.constraints), table.schema) for table in CredentialBase.metadata.sorted_tables}
    sql = migration.render_workflow_credentials_sql()
    assert len(CredentialBase.metadata.tables) == 1
    assert len(Base.metadata.tables) == 4
    assert len(SourceBase.metadata.tables) == 2
    assert len(SetupBase.metadata.tables) == 1
    assert before == {
        table.name: (len(table.constraints), table.schema) for table in CredentialBase.metadata.sorted_tables
    }
    for name in migration.prerequisite_metadata().tables:
        assert f"CREATE TABLE public.{name} (" not in sql
        assert f"ALTER TABLE public.{name} " not in sql
    assert "ciphertext TEXT NOT NULL" in sql
    assert "PRIMARY KEY (workspace_id, app_id, secret_ref)" in sql


def test_ci_runs_credential_migration_after_setup_smoke():
    from pathlib import Path

    workflow = Path(__file__).resolve().parents[4] / ".github/workflows/enterprise-foundation.yml"
    content = workflow.read_text(encoding="utf-8")
    source_smoke = content.index("--ignore=tests/integration/test_workflow_setups.py")
    setup_migrate = content.index("python -m enterprise_platform.persistence.migrate_workflow_setups")
    setup_smoke = content.index("tests/integration/test_workflow_setups.py -q")
    credential_migrate = content.index("python -m enterprise_platform.persistence.migrate_workflow_credentials")
    credential_smoke = content.index("tests/integration/test_workflow_credentials.py -q", credential_migrate)
    assert source_smoke < setup_migrate < setup_smoke < credential_migrate < credential_smoke
    assert "--ignore=tests/integration/test_workflow_credentials.py" in content[source_smoke:setup_migrate]
    assert "ENTERPRISE_CI_CREDENTIAL_MIGRATED_PUBLIC: '1'" in content[credential_smoke:]
