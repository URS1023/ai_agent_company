import json
from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.office_edits import OfficeFileRecord
from enterprise_platform.domain.office_content import TableData
from enterprise_platform.domain.office_revision import OfficeRevision, OfficeUnit
from enterprise_platform.persistence.models import Base
from enterprise_platform.persistence.office_documents import decode_office_record, encode_office_record
from enterprise_platform.persistence.office_models import OfficeBase


def record() -> OfficeFileRecord:
    return OfficeFileRecord(
        workspace_id="workspace",
        template_id="industrial-orange",
        template_revision=1,
        source_snapshot_ids=("snapshot-1",),
        content=OfficeRevision(
            file_id=UUID(int=1),
            revision=1,
            kind="document",
            units=(
                OfficeUnit(
                    unit_id=UUID(int=2),
                    kind="table",
                    content=(
                        TableData(
                            table_id="quality",
                            headers=("ID", "Decimal", "Missing", "Integer"),
                            rows=(("0001", Decimal("1.000000000000000001"), None, 10**40),),
                        ),
                    ),
                ),
            ),
        ),
    )


def test_storage_roundtrip_preserves_complete_typed_record_and_bindings() -> None:
    original = record()
    encoded = encode_office_record(original)
    restored = decode_office_record(encoded)
    assert restored.fingerprint() == original.fingerprint()
    assert json.loads(encoded)["schema_version"] == 1
    assert encode_office_record(restored) == encoded


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("schema_version", 1.0),
        ("template_revision", True),
        ("template_revision", 0),
        ("workspace_id", ""),
        ("source_snapshot_ids", ["snapshot-1", "snapshot-1"]),
    ],
)
def test_invalid_storage_metadata_is_rejected(field: str, value: object) -> None:
    document = json.loads(encode_office_record(record()))
    document[field] = value
    with pytest.raises(PersistenceError):
        decode_office_record(json.dumps(document))


def test_encoder_rejects_invalid_internal_record_instead_of_coercing_it() -> None:
    with pytest.raises(PersistenceError):
        encode_office_record(replace(record(), template_revision=True))


def test_storage_requires_explicit_schema_version() -> None:
    document = json.loads(encode_office_record(record()))
    del document["schema_version"]
    with pytest.raises(PersistenceError):
        decode_office_record(json.dumps(document))


@pytest.mark.parametrize("document", ["not-json", "{}", "null", '{"schema_version":1,"unexpected":true}'])
def test_malformed_record_errors_do_not_echo_document(document: str) -> None:
    with pytest.raises(PersistenceError, match="^office_document_invalid$"):
        decode_office_record(document)


def test_office_schema_is_isolated_and_has_scoped_history_and_receipt_keys() -> None:
    tables = OfficeBase.metadata.tables
    assert set(tables) == {
        "enterprise_office_files",
        "enterprise_office_grants",
        "enterprise_office_revisions",
        "enterprise_office_edit_receipts",
    }
    assert not set(tables).intersection(Base.metadata.tables)
    assert list(tables["enterprise_office_revisions"].primary_key.columns.keys()) == [
        "workspace_id",
        "file_id",
        "revision",
    ]
    assert list(tables["enterprise_office_edit_receipts"].primary_key.columns.keys()) == [
        "workspace_id",
        "file_id",
        "actor_id",
        "request_id",
    ]
    for table in tables.values():
        sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE" in sql
    foreign_keys = list(tables["enterprise_office_edit_receipts"].foreign_key_constraints)
    assert any(
        [column.name for column in key.columns] == ["workspace_id", "file_id", "result_revision"]
        for key in foreign_keys
    )
