"""Workspace-owned, append-only source configuration. Saving never probes a source.

An idempotent create replays its original version, not the current mutable head.
Captured runs bypass this catalog entirely; uncollected versions obey current
operator egress policy and require their original encryption key.
"""

import hmac
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from enterprise_platform.domain.data_sources import DataSourceError, SourceRef

from .contracts import Device, Page, Principal
from .errors import AccessDenied, Conflict, DependencyUnavailable, InvalidInput, NotFound, PersistenceError
from .input_capture import ImmutableReadCatalog, RegisteredRead
from .source_contracts import SourceCapabilities, SourceDraft, SourceView
from .source_ports import SourceCipher, SourceRepository, StoredSource
from .source_registration import EndpointPolicy, registration


class DeviceLookup(Protocol):
    def get_device(self, workspace_id: str, device_id: str, /) -> Device: ...


class SourceService:
    def __init__(
        self,
        repository: SourceRepository,
        devices: DeviceLookup,
        *,
        cipher: SourceCipher | None,
        endpoints: EndpointPolicy,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository, self._devices, self._cipher, self._endpoints = repository, devices, cipher, endpoints
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id = id_factory or (lambda: str(uuid4()))

    def capabilities(self, principal: Principal) -> SourceCapabilities:
        return SourceCapabilities(
            can_manage=principal.workspace_role in {"owner", "admin"},
            write_enabled=self._cipher is not None and bool(self._endpoints.entries),
            reason_code="encryption_key_missing"
            if self._cipher is None
            else ("source_egress_policy_missing" if not self._endpoints.entries else None),
        )

    def _write(self, principal: Principal) -> SourceCipher:
        if principal.workspace_role not in {"owner", "admin"}:
            raise AccessDenied()
        if self._cipher is None or not self._endpoints.entries:
            raise DependencyUnavailable(self.capabilities(principal).reason_code)
        return self._cipher

    @staticmethod
    def _view(principal: Principal, view: SourceView) -> SourceView:
        if not principal.can("read") or view.workspace_id != principal.workspace_id:
            raise AccessDenied()
        return view

    def list_sources(self, principal: Principal, *, offset: int = 0, limit: int = 20) -> Page[SourceView]:
        if not principal.can("read"):
            raise AccessDenied()
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("invalid_pagination")
        page = self._repository.list(principal.workspace_id, offset=offset, limit=limit)
        for view in page.items:
            self._view(principal, view)
        return page

    def get_source(self, principal: Principal, source_id: str) -> SourceView:
        return self._view(principal, self._repository.get(principal.workspace_id, source_id).view)

    def _prepare(self, principal: Principal, draft: SourceDraft) -> SourceDraft:
        try:
            valid = SourceDraft.model_validate(draft.model_dump())
        except ValidationError:
            raise InvalidInput("invalid_source_configuration") from None
        self._endpoints.require(valid.connection)
        for device_id in valid.device_ids:
            device = self._devices.get_device(principal.workspace_id, device_id)
            if (device.workspace_id, device.id) != (principal.workspace_id, device_id):
                raise AccessDenied("source_device_scope_mismatch")
        return valid

    def _build(
        self,
        principal: Principal,
        draft: SourceDraft,
        *,
        previous: StoredSource | None,
        cipher: SourceCipher,
        request_key: str,
        request_hash: str,
        fingerprint_key_id: str,
    ) -> StoredSource:
        draft = self._prepare(principal, draft)
        now = self._clock()
        old = (previous.view, cipher.open(previous.view, previous.sealed)) if previous is not None else None
        source_id = previous.view.source_id if previous is not None else self._id()
        read_id = previous.view.read_id if previous is not None else self._id()
        source = SourceRef(workspace_id=principal.workspace_id, source_id=source_id, revision=self._id())
        read_revision = self._id()
        try:
            public, entry = registration(
                draft, source=source, read_id=read_id, read_revision=read_revision, previous=old
            )
            view = SourceView.model_validate(
                {
                    **draft.model_dump(exclude={"connection", "read_only_confirmed"}),
                    "workspace_id": principal.workspace_id,
                    "source_id": source_id,
                    "source_revision": source.revision,
                    "read_id": read_id,
                    "read_revision": read_revision,
                    "revision": previous.view.revision + 1 if previous else 1,
                    "connection": public,
                    "created_at": previous.view.created_at if previous else now,
                    "updated_at": now,
                }
            )
        except (ValueError, DataSourceError):
            raise InvalidInput("invalid_source_configuration") from None
        return StoredSource(view, cipher.seal(view, entry), request_key, request_hash, fingerprint_key_id)

    def _replay(
        self, principal: Principal, draft: SourceDraft, source: StoredSource, cipher: SourceCipher
    ) -> SourceView:
        _, fingerprint = cipher.fingerprint(draft, key_id=source.fingerprint_key_id)
        if not hmac.compare_digest(fingerprint, source.request_hash):
            raise Conflict("idempotency_key_reused")
        return self._view(principal, source.view)

    def create_source(self, principal: Principal, draft: SourceDraft, *, request_key: str) -> SourceView:
        cipher = self._write(principal)
        if not re.fullmatch(r"[\x21-\x7e]{1,128}", request_key):
            raise InvalidInput("invalid_idempotency_key")
        existing = self._repository.find_request(principal.workspace_id, request_key)
        if existing is not None:
            return self._replay(principal, draft, existing, cipher)
        key_id, fingerprint = cipher.fingerprint(draft)
        source = self._build(
            principal,
            draft,
            previous=None,
            cipher=cipher,
            request_key=request_key,
            request_hash=fingerprint,
            fingerprint_key_id=key_id,
        )
        try:
            saved = self._repository.create(source, actor_id=principal.actor_id)
        except Conflict:
            existing = self._repository.find_request(principal.workspace_id, request_key)
            if existing is None:
                raise
            return self._replay(principal, draft, existing, cipher)
        return self._view(principal, saved.view)

    def update_source(
        self, principal: Principal, source_id: str, draft: SourceDraft, *, expected_revision: int
    ) -> SourceView:
        cipher = self._write(principal)
        if type(expected_revision) is not int or expected_revision < 1:
            raise InvalidInput("invalid_revision")
        previous = self._repository.get(principal.workspace_id, source_id)
        self._view(principal, previous.view)
        if previous.view.revision != expected_revision:
            raise Conflict("concurrent_revision_conflict")
        source = self._build(
            principal,
            draft,
            previous=previous,
            cipher=cipher,
            request_key=previous.request_key,
            request_hash=previous.request_hash,
            fingerprint_key_id=previous.fingerprint_key_id,
        )
        return self._view(
            principal,
            self._repository.update(source, expected_revision=expected_revision, actor_id=principal.actor_id).view,
        )


class StoredReadCatalog:
    def __init__(
        self,
        repository: SourceRepository,
        cipher: SourceCipher,
        endpoints: EndpointPolicy,
        static: ImmutableReadCatalog,
    ) -> None:
        self._repository, self._cipher, self._endpoints, self._static = repository, cipher, endpoints, static

    def _require_enabled_head(self, workspace_id: str, source_id: str) -> None:
        current_source = self._repository.get(workspace_id, source_id)
        current = current_source.view
        if (current.workspace_id, current.source_id) != (workspace_id, source_id):
            raise PersistenceError("source_registration_scope_mismatch")
        self._cipher.open(current, current_source.sealed)
        if not current.enabled:
            raise AccessDenied("source_disabled")

    def resolve(
        self, workspace_id: str, source_id: str, source_revision: str, read_id: str, read_revision: str
    ) -> RegisteredRead:
        key = (workspace_id, source_id, source_revision, read_id, read_revision)
        source = self._repository.find_read(*key)
        try:
            static = self._static.resolve(*key)
        except InvalidInput:
            static = None
        if source is None:
            if static is None:
                raise InvalidInput("registered_read_unavailable")
            try:
                self._require_enabled_head(workspace_id, source_id)
            except NotFound:
                pass
            return static
        if static is not None:
            raise Conflict("duplicate_registered_read")
        view = source.view
        if (view.workspace_id, view.source_id, view.source_revision, view.read_id, view.read_revision) != key:
            raise PersistenceError("source_registration_scope_mismatch")
        self._require_enabled_head(workspace_id, source_id)
        self._endpoints.require(view.connection)
        return self._cipher.open(view, source.sealed)
