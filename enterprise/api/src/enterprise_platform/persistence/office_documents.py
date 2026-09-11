"""Strict, versioned Office storage JSON; not an HTTP or renderer input format."""

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from enterprise_platform.application.contracts import Identifier
from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.office_edits import OfficeFileRecord
from enterprise_platform.domain.office_content import OfficeContent
from enterprise_platform.domain.office_revision import OfficeRevision


class _StoredRecord(OfficeContent):
    schema_version: Literal[1]
    workspace_id: Identifier
    template_id: Identifier
    template_revision: int = Field(ge=1, le=9223372036854775807)
    source_snapshot_ids: tuple[Identifier, ...] = Field(max_length=10000)
    content: OfficeRevision

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Storage schema version must be an integer")
        return value

    @model_validator(mode="after")
    def unique_sources(self) -> Self:
        if len(set(self.source_snapshot_ids)) != len(self.source_snapshot_ids):
            raise ValueError("Duplicate source snapshot")
        if self.content.revision > 9223372036854775807:
            raise ValueError("Revision exceeds storage range")
        return self

    def record(self) -> OfficeFileRecord:
        return OfficeFileRecord(
            workspace_id=self.workspace_id,
            template_id=self.template_id,
            template_revision=self.template_revision,
            source_snapshot_ids=self.source_snapshot_ids,
            content=self.content,
        )


def encode_office_record(record: OfficeFileRecord) -> str:
    try:
        stored = _StoredRecord(
            schema_version=1,
            workspace_id=record.workspace_id,
            template_id=record.template_id,
            template_revision=record.template_revision,
            source_snapshot_ids=record.source_snapshot_ids,
            content=record.content,
        )
        document = stored.model_dump_json()
        if decode_office_record(document).fingerprint() != record.fingerprint():
            raise ValueError("Office record type changed during serialization")
        return document
    except (ValueError, TypeError, ArithmeticError):
        raise PersistenceError("office_document_invalid") from None


def decode_office_record(document: str) -> OfficeFileRecord:
    try:
        return _StoredRecord.model_validate_json(document).record()
    except (ValueError, TypeError, ArithmeticError):
        raise PersistenceError("office_document_invalid") from None
