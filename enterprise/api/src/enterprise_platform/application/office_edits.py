"""Authorized Office edit orchestration; not exposed until durable ports are wired.

The repository must atomically fence file and ACL revisions and store both the new
file revision and replay receipt. A service-level check alone is not concurrency
control. Read/replay never renders, refreshes sources or creates an artifact task.
"""

from dataclasses import dataclass, replace
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, ValidationError

from enterprise_platform.domain.office_content import OfficeContent
from enterprise_platform.domain.office_revision import (
    OfficeRevision,
    OfficeRevisionError,
    UnitReplacement,
    replace_units,
)

from .contracts import Principal, canonical_hash
from .errors import AccessDenied, Conflict, InvalidInput, PersistenceError

type OfficeAction = Literal["read", "edit"]


@dataclass(frozen=True, slots=True)
class OfficeGrant:
    workspace_id: str
    actor_id: str
    file_id: UUID
    action: OfficeAction
    acl_revision: int


@dataclass(frozen=True, slots=True)
class OfficeFileRecord:
    workspace_id: str
    template_id: str
    template_revision: int
    source_snapshot_ids: tuple[str, ...]
    content: OfficeRevision

    def fingerprint(self) -> str:
        """Retain decimal cell types and scale; numeric equality would hide coercion."""
        return canonical_hash(
            {
                "workspace_id": self.workspace_id,
                "template_id": self.template_id,
                "template_revision": self.template_revision,
                "source_snapshot_ids": list(self.source_snapshot_ids),
                "content": self.content.model_dump(mode="json"),
            }
        )


class OfficeEditCommand(OfficeContent):
    request_id: UUID
    expected_revision: int = Field(ge=1)
    replacements: tuple[UnitReplacement, ...] = Field(min_length=1, max_length=10000)


@dataclass(frozen=True, slots=True)
class OfficeEditReceipt:
    workspace_id: str
    actor_id: str
    file_id: UUID
    request_id: UUID
    command_hash: str
    record: OfficeFileRecord


class OfficeFileCreator(Protocol):
    def create(self, principal: Principal, record: OfficeFileRecord) -> OfficeFileRecord: ...


class OfficeAccessPolicy(Protocol):
    def authorize(self, principal: Principal, file_id: UUID, action: OfficeAction) -> OfficeGrant:
        """Check current file/source permissions; return a versioned grant or raise AccessDenied."""
        ...


class OfficeEditRepository(Protocol):
    def get(self, grant: OfficeGrant) -> OfficeFileRecord:
        """Read under the current ACL version; a revoked/stale grant must fail."""
        ...

    def find_receipt(self, grant: OfficeGrant, request_id: UUID) -> OfficeEditReceipt | None:
        """Read actor/file-scoped receipts under current authorization, including source access."""
        ...

    def commit(
        self,
        *,
        grant: OfficeGrant,
        expected: OfficeFileRecord,
        candidate: OfficeFileRecord,
        request_id: UUID,
        command_hash: str,
    ) -> OfficeEditReceipt:
        """Atomically recheck ACL, expected revision and bindings, then append revision + receipt.

        A matching receipt wins over a stale expected revision after a concurrent
        identical request. Changed request content or any other conflict raises
        Conflict without a write. Never replace an existing historical revision.
        """
        ...


class OfficeEditService:
    def __init__(self, repository: OfficeEditRepository, policy: OfficeAccessPolicy) -> None:
        self._repository, self._policy = repository, policy

    def _grant(self, principal: Principal, file_id: UUID, action: OfficeAction) -> OfficeGrant:
        grant = self._policy.authorize(principal, file_id, action)
        if (
            (grant.workspace_id, grant.actor_id, grant.file_id, grant.action)
            != (principal.workspace_id, principal.actor_id, file_id, action)
            or type(grant.acl_revision) is not int
            or grant.acl_revision < 1
        ):
            raise AccessDenied()
        return grant

    @staticmethod
    def _check_record(grant: OfficeGrant, record: OfficeFileRecord) -> None:
        if (record.workspace_id, record.content.file_id) != (grant.workspace_id, grant.file_id):
            raise AccessDenied()

    def read(self, principal: Principal, file_id: UUID) -> OfficeFileRecord:
        grant = self._grant(principal, file_id, "read")
        record = self._repository.get(grant)
        self._check_record(grant, record)
        return record

    @staticmethod
    def _check_receipt(grant: OfficeGrant, command: OfficeEditCommand, receipt: OfficeEditReceipt) -> None:
        if (receipt.workspace_id, receipt.actor_id, receipt.file_id, receipt.request_id) != (
            grant.workspace_id,
            grant.actor_id,
            grant.file_id,
            command.request_id,
        ) or (receipt.record.workspace_id, receipt.record.content.file_id, receipt.record.content.revision) != (
            grant.workspace_id,
            grant.file_id,
            command.expected_revision + 1,
        ):
            raise PersistenceError("office_edit_receipt_mismatch")

    def _find_previous(self, grant: OfficeGrant, command: OfficeEditCommand, digest: str) -> OfficeEditReceipt | None:
        previous = self._repository.find_receipt(grant, command.request_id)
        if previous is not None:
            if previous.command_hash != digest:
                raise Conflict("office_request_reused")
            self._check_receipt(grant, command, previous)
        return previous

    def edit(self, principal: Principal, file_id: UUID, command: OfficeEditCommand) -> OfficeEditReceipt:
        grant = self._grant(principal, file_id, "edit")
        digest = canonical_hash(command.model_dump(mode="json"))
        previous = self._find_previous(grant, command, digest)
        if previous is not None:
            return previous
        current = self._repository.get(grant)
        self._check_record(grant, current)
        try:
            content = replace_units(
                current.content,
                file_id=file_id,
                expected_revision=command.expected_revision,
                replacements=command.replacements,
            )
        except OfficeRevisionError as error:
            if error.code == "revision_conflict":
                # An identical request may have committed after the initial receipt lookup.
                previous = self._find_previous(grant, command, digest)
                if previous is not None:
                    return previous
                raise Conflict("office_revision_conflict") from error
            raise InvalidInput(f"office_{error.code}") from error
        except ValidationError as error:
            raise InvalidInput("office_unit_content_invalid") from error
        candidate = replace(current, content=content)
        receipt = self._repository.commit(
            grant=grant,
            expected=current,
            candidate=candidate,
            request_id=command.request_id,
            command_hash=digest,
        )
        self._check_receipt(grant, command, receipt)
        if receipt.command_hash != digest or receipt.record.fingerprint() != candidate.fingerprint():
            raise PersistenceError("office_edit_receipt_mismatch")
        return receipt
