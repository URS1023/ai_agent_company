"""Durable import ownership; no automatic re-submission of importing operations."""

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import TypeAdapter

from .contracts import Identifier, Page, Scenario
from .workflow_setup_contracts import SetupFinalState, SetupView


@dataclass(frozen=True)
class StoredSetup:
    view: SetupView
    actor_id: str
    request_key: str
    request_hash: str
    import_nonce: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        identifier = TypeAdapter(Identifier)
        identifier.validate_python(self.actor_id)
        identifier.validate_python(self.request_key)
        if len(self.request_hash) != 64 or any(c not in "0123456789abcdef" for c in self.request_hash):
            raise ValueError("Invalid setup request hash")
        if self.import_nonce is not None:
            identifier.validate_python(self.import_nonce)
        if self.view.state == "importing" and self.import_nonce is None:
            raise ValueError("Import ownership required")
        if self.view.state != "importing" and self.import_nonce is not None:
            raise ValueError("Import ownership only exists while importing")


class WorkflowSetupRepository(Protocol):
    def find_request(self, workspace_id: str, request_key: str) -> StoredSetup | None: ...
    def create(self, setup: StoredSetup, *, actor_id: str) -> StoredSetup: ...
    def get(self, workspace_id: str, setup_id: str) -> StoredSetup: ...
    def list(
        self, workspace_id: str, *, device_id: str, scenario: Scenario, offset: int, limit: int
    ) -> Page[SetupView]: ...
    def claim_import(
        self, workspace_id: str, setup_id: str, *, expected_revision: int, nonce: str, actor_id: str
    ) -> StoredSetup: ...
    def finish_import(
        self,
        workspace_id: str,
        setup_id: str,
        *,
        nonce: str,
        state: SetupFinalState,
        app_id: str | None,
        import_id: str | None,
        reason_code: str | None,
        actor_id: str,
    ) -> StoredSetup: ...
