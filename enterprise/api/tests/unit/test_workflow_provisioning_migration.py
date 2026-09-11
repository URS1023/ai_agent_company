"""Guarded 0005 migration behavior with injected engines only; no database connections."""

import importlib
import importlib.util
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from enterprise_platform.application.errors import Conflict, InvalidInput, PersistenceError


@pytest.fixture
def migration():
    name = "enterprise_platform.persistence.migrate_workflow_provisioning"
    assert importlib.util.find_spec(name) is not None, "Guarded provisioning migration must exist"
    return importlib.import_module(name)


def inspector(migration):
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


def test_requires_exact_eight_table_prerequisites(migration):
    from enterprise_platform.persistence.migrate_workflow_credentials import require_credential_prerequisites

    assert len(migration.prerequisite_metadata().tables) == 8
    migration.require_provisioning_prerequisites(inspector(migration))
    with pytest.raises(Conflict):
        require_credential_prerequisites(inspector(migration))


@pytest.mark.parametrize(
    "change", ["missing_credential", "extra", "columns", "pk_constraint", "indexes", "check_constraints"]
)
def test_prerequisite_drift_is_rejected(migration, change):
    fake = inspector(migration)
    if change == "missing_credential":
        fake.get_table_names.return_value.remove("enterprise_workflow_credentials")
    elif change == "extra":
        fake.get_table_names.return_value.append("apps")
    else:
        method = getattr(fake, f"get_{change}")
        original = method.side_effect
        method.side_effect = lambda name, **kw: (
            ({"constrained_columns": []} if change == "pk_constraint" else [])
            if name == "enterprise_workflow_credentials"
            else original(name, **kw)
        )
    with pytest.raises(Conflict):
        migration.require_provisioning_prerequisites(fake)


def test_artifact_print_is_exact_additive_and_does_not_connect(migration, monkeypatch, capsys):
    from enterprise_platform.persistence.workflow_provisioning_models import ProvisioningBase

    before = {table.name: (len(table.constraints), table.schema) for table in ProvisioningBase.metadata.sorted_tables}
    sql = migration.read_workflow_provisioning_sql()
    assert sql == migration.render_workflow_provisioning_sql()
    assert sql == migration.render_workflow_provisioning_sql()
    assert before == {
        table.name: (len(table.constraints), table.schema) for table in ProvisioningBase.metadata.sorted_tables
    }
    assert "BEGIN;\nSET LOCAL search_path TO public;" in sql
    assert sql.endswith("COMMIT;\n")
    assert "IF NOT EXISTS" not in sql and "DROP " not in sql
    for name in migration.prerequisite_metadata().tables:
        assert f"CREATE TABLE public.{name} (" not in sql
        assert f"ALTER TABLE public.{name} " not in sql
    for name in ProvisioningBase.metadata.tables:
        assert f"CREATE TABLE public.{name} (" in sql
    monkeypatch.setattr(migration, "create_engine", Mock(side_effect=AssertionError("No database")))
    assert migration.main(["--print-sql"]) == 0
    assert capsys.readouterr().out == sql


def test_artifact_drift_is_rejected_before_engine(migration, monkeypatch):
    package = Mock()
    package.joinpath.return_value.read_text.return_value = "unexpected artifact"
    monkeypatch.setattr(migration, "files", Mock(return_value=package))
    engine = Mock(side_effect=AssertionError("No database"))
    monkeypatch.setattr(migration, "create_engine", engine)
    with pytest.raises(InvalidInput, match="migration_artifact_mismatch"):
        migration.apply_workflow_provisioning(
            "postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test"
        )
    engine.assert_not_called()


@pytest.mark.parametrize("url", ["postgresql+psycopg://user:secret@host/dify", "sqlite:///enterprise_test.db"])
def test_wrong_target_precedes_engine(migration, monkeypatch, url):
    engine = Mock(side_effect=AssertionError("No database"))
    monkeypatch.setattr(migration, "create_engine", engine)
    with pytest.raises(InvalidInput):
        migration.apply_workflow_provisioning(url, "enterprise_test")
    engine.assert_not_called()


def test_actual_database_mismatch_precedes_any_sql(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "other"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    with pytest.raises(InvalidInput):
        migration.apply_workflow_provisioning(
            "postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test"
        )
    connection.exec_driver_sql.assert_not_called()
    engine.dispose.assert_called_once()


def test_apply_is_transactional_locked_and_guarded(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=inspector(migration)))
    migration.apply_workflow_provisioning("postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test")
    assert "1162761296" in str(connection.execute.call_args.args[0])
    assert [call.args[0] for call in connection.exec_driver_sql.call_args_list] == [
        "SET LOCAL lock_timeout = '10s'",
        "SET LOCAL statement_timeout = '30s'",
        "SET LOCAL search_path TO public",
        *migration.workflow_provisioning_statements(),
    ]
    engine.begin.return_value.__exit__.assert_called_once_with(None, None, None)
    engine.dispose.assert_called_once()


def test_failed_schema_guard_precedes_additive_ddl(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    fake = inspector(migration)
    fake.get_table_names.return_value.append("extra")
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=fake))
    with pytest.raises(Conflict):
        migration.apply_workflow_provisioning(
            "postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test"
        )
    assert len(connection.exec_driver_sql.call_args_list) == 3
    assert engine.begin.return_value.__exit__.call_args.args[0] is Conflict
    engine.dispose.assert_called_once()


def test_database_failure_is_sanitized_and_disposes(migration, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    engine = MagicMock()
    engine.begin.return_value.__enter__.side_effect = SQLAlchemyError("password=private")
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    with pytest.raises(PersistenceError) as failure:
        migration.apply_workflow_provisioning(
            "postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test"
        )
    assert "private" not in str(failure.value)
    assert failure.value.__suppress_context__
    engine.dispose.assert_called_once()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--apply"],
        ["--private-secret"],
        ["--apply", "--url-env", "DATABASE_URL", "--expected-database", "enterprise_test"],
    ],
)
def test_cli_rejects_implicit_targets_and_sanitizes(migration, arguments, capsys):
    assert migration.main(arguments) == 1
    captured = capsys.readouterr()
    assert captured.err == "invalid_input\n"
    assert captured.out == ""


def test_cli_explicit_environment_and_success_message(migration, monkeypatch, capsys):
    apply = Mock()
    monkeypatch.setattr(migration, "apply_workflow_provisioning", apply)
    monkeypatch.setenv("ENTERPRISE_PROVISIONING_DATABASE_URL", "postgresql+psycopg://user:secret@host/enterprise_test")
    assert (
        migration.main(
            ["--apply", "--url-env", "ENTERPRISE_PROVISIONING_DATABASE_URL", "--expected-database", "enterprise_test"]
        )
        == 0
    )
    apply.assert_called_once_with("postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test")
    assert capsys.readouterr().out == "Applied enterprise migration 0005.\n"


def test_guarded_migration_entrypoint_exists():
    assert importlib.util.find_spec("enterprise_platform.persistence.migrate_workflow_provisioning") is not None


def test_cli_missing_environment_never_connects(migration, monkeypatch, capsys):
    monkeypatch.delenv("ENTERPRISE_PROVISIONING_DATABASE_URL", raising=False)
    engine = Mock(side_effect=AssertionError("No database"))
    monkeypatch.setattr(migration, "create_engine", engine)
    assert (
        migration.main(
            ["--apply", "--url-env", "ENTERPRISE_PROVISIONING_DATABASE_URL", "--expected-database", "enterprise_test"]
        )
        == 1
    )
    engine.assert_not_called()
    assert capsys.readouterr().err == "invalid_input\n"


def test_ddl_has_exact_scoped_journal_key_constraints_and_index(migration):
    sql = migration.read_workflow_provisioning_sql()
    assert "PRIMARY KEY (workspace_id, provisioning_id)" in sql
    assert "UNIQUE (workspace_id, request_key)" in sql
    assert "claim_nonce VARCHAR(36)" in sql
    assert "public_json TEXT NOT NULL" in sql
    assert "ck_workflow_provisioning_nonce" in sql
    assert "ck_workflow_provisioning_phase_count" in sql
    assert "CREATE INDEX ix_workflow_provisioning_setup ON public.enterprise_workflow_provisioning" in sql
    assert "ciphertext" not in sql
    assert "password" not in sql


def test_reapplication_is_rejected_before_ddl(migration, monkeypatch):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "enterprise_test"
    fake = inspector(migration)
    fake.get_table_names.return_value.append("enterprise_workflow_provisioning")
    monkeypatch.setattr(migration, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(migration, "inspect", Mock(return_value=fake))
    with pytest.raises(Conflict):
        migration.apply_workflow_provisioning(
            "postgresql+psycopg://user:secret@host/enterprise_test", "enterprise_test"
        )
    assert len(connection.exec_driver_sql.call_args_list) == 3
    engine.dispose.assert_called_once()


def test_postgres_reflected_credential_checks_pass_eight_table_guard(migration):
    fake = inspector(migration)
    original = fake.get_check_constraints.side_effect
    reflected = {
        "ck_workflow_credential_revision": "(revision > 0)",
        "ck_workflow_credential_key": "((length((key_id)::text) >= 1) AND (length((key_id)::text) <= 64))",
        "ck_workflow_credential_nonce": "(length((nonce)::text) = 16)",
        "ck_workflow_credential_payload": "((length(ciphertext) >= 24) AND (length(ciphertext) <= 5484))",
    }
    fake.get_check_constraints.side_effect = lambda name, **kw: (
        [dict(name=key, sqltext=value) for key, value in reflected.items()]
        if name == "enterprise_workflow_credentials"
        else original(name, **kw)
    )
    migration.require_provisioning_prerequisites(fake)


@pytest.mark.parametrize(
    "sql",
    [
        "length((nonce)::text) = 15",
        "length((key_id)::text) = 16",
        "octet_length((nonce)::text) = 16",
        "length((nonce)::varchar(8)) = 16",
    ],
)
def test_postgres_reflected_credential_drift_still_rejected(migration, sql):
    fake = inspector(migration)
    original = fake.get_check_constraints.side_effect

    def checks(name, **kw):
        result = original(name, **kw)
        if name == "enterprise_workflow_credentials":
            for item in result:
                if item["name"] == "ck_workflow_credential_nonce":
                    item["sqltext"] = sql
        return result

    fake.get_check_constraints.side_effect = checks
    with pytest.raises(Conflict):
        migration.require_provisioning_prerequisites(fake)
