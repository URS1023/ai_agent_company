"""Source management contracts are independent from native Dify storage."""

from dataclasses import dataclass
from typing import Protocol

from .contracts import Page, Principal
from .input_capture import RegisteredRead
from .source_contracts import SourceCapabilities, SourceDraft, SourceView


@dataclass(frozen=True)
class SealedSource:
    key_id: str
    nonce: str
    ciphertext: str


@dataclass(frozen=True)
class StoredSource:
    view: SourceView
    sealed: SealedSource
    request_key: str
    request_hash: str
    fingerprint_key_id: str


class SourceRepository(Protocol):
    def find_request(self, workspace_id: str, request_key: str) -> StoredSource | None: ...
    def create(self, source: StoredSource, *, actor_id: str) -> StoredSource: ...
    def update(self, source: StoredSource, *, expected_revision: int, actor_id: str) -> StoredSource: ...
    def get(self, workspace_id: str, source_id: str) -> StoredSource: ...
    def list(self, workspace_id: str, *, offset: int, limit: int) -> Page[SourceView]: ...
    def find_read(
        self, workspace_id: str, source_id: str, source_revision: str, read_id: str, read_revision: str
    ) -> StoredSource | None: ...


class SourceCipher(Protocol):
    def seal(self, view: SourceView, registration: RegisteredRead) -> SealedSource: ...
    def open(self, view: SourceView, sealed: SealedSource) -> RegisteredRead: ...
    def fingerprint(self, draft: SourceDraft, *, key_id: str | None = None) -> tuple[str, str]: ...


class SourceManagement(Protocol):
    def capabilities(self, principal: Principal) -> SourceCapabilities: ...

    def list_sources(self, principal: Principal, *, offset: int = 0, limit: int = 20) -> Page[SourceView]: ...

    def get_source(self, principal: Principal, source_id: str) -> SourceView: ...

    def create_source(self, principal: Principal, draft: SourceDraft, *, request_key: str) -> SourceView: ...

    def update_source(
        self, principal: Principal, source_id: str, draft: SourceDraft, *, expected_revision: int
    ) -> SourceView: ...
