from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable
from test_source_encryption import keyring, registration_pair

from enterprise_platform.adapters.source_encryption import AesGcmSourceCipher
from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.source_ports import StoredSource
from enterprise_platform.persistence.source_models import SourceBase, SourceHeadRow, SourceVersionRow
from enterprise_platform.persistence.sources import SqlAlchemySourceRepository, as_source, source_version_row


def stored():
    view, entry = registration_pair()
    cipher = AesGcmSourceCipher(keyring())
    return StoredSource(view, cipher.seal(view, entry), "request-1", "a" * 64, "key-1")


def test_source_metadata_is_separate_from_published_four_table_initial() -> None:
    from enterprise_platform.persistence.migrate import read_initial_sql
    from enterprise_platform.persistence.models import Base

    assert len(Base.metadata.tables) == 4
    assert set(SourceBase.metadata.tables) == {"enterprise_source_heads", "enterprise_source_versions"}
    assert read_initial_sql().count("CREATE TABLE") == 4
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        for table in SourceBase.metadata.sorted_tables:
            sql = str(CreateTable(table).compile(dialect=dialect))
            assert "workspace_id" in sql
            assert not {"password", "connection_url", "username", "headers"} & set(table.c.keys())
    assert tuple(SourceHeadRow.__table__.primary_key.columns.keys()) == ("workspace_id", "source_id")
    assert tuple(SourceVersionRow.__table__.primary_key.columns.keys()) == ("workspace_id", "source_id", "revision")
    fk = next(iter(SourceVersionRow.__table__.foreign_key_constraints))
    assert fk.column_keys == ["workspace_id", "source_id"]


def test_ciphertext_row_roundtrip_and_public_json_never_contains_secret() -> None:
    value = stored()
    row = source_version_row(value)
    assert "fixture-password" not in row.public_json and "fixture-password" not in row.ciphertext
    assert as_source(row) == value
    for field, new in [("workspace_id", "other"), ("source_revision", "other"), ("revision", 999)]:
        corrupted = source_version_row(value)
        setattr(corrupted, field, new)
        with pytest.raises(PersistenceError):
            as_source(corrupted)


def test_repository_construction_does_not_open_session() -> None:
    sessions = Mock(side_effect=AssertionError("must not connect"))
    SqlAlchemySourceRepository(sessions)
    sessions.assert_not_called()


def test_exact_read_lookup_compiles_all_five_scope_fields_without_following_head() -> None:
    from unittest.mock import MagicMock, create_autospec

    from sqlalchemy.orm import Session

    session = create_autospec(Session, instance=True)
    session.connection.return_value.dialect.name = "postgresql"
    session.scalar.return_value = source_version_row(stored())
    sessions = MagicMock()
    sessions.begin.return_value.__enter__.return_value = session
    repo = SqlAlchemySourceRepository(sessions)
    view = stored().view
    found = repo.find_read(view.workspace_id, view.source_id, view.source_revision, view.read_id, view.read_revision)
    assert found.view == view
    statement = session.scalar.call_args.args[0]
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        compiled = statement.compile(dialect=dialect)
        assert set(compiled.params.values()) == {
            view.workspace_id,
            view.source_id,
            view.source_revision,
            view.read_id,
            view.read_revision,
        }
        assert "enterprise_source_heads" not in str(compiled)
        assert str(compiled).count(" = ") == 5
