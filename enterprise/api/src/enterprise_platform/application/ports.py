"""Narrow contracts for business persistence and native identity verification."""

from typing import Protocol

from .contracts import (
    AuditEvent,
    Binding,
    BindingWrite,
    BusinessResult,
    Device,
    DeviceCreate,
    DeviceUpdate,
    JsonObject,
    Page,
    Principal,
    Run,
    RunSpec,
    Scenario,
)


class IdentityProvider(Protocol):
    async def resolve(
        self, *, cookie_header: str | None, authorization: str | None, csrf_token: str | None
    ) -> Principal: ...


class EnterpriseRepository(Protocol):
    def create_device(self, ws: str, payload: DeviceCreate, /, *, actor_id: str) -> Device: ...
    def get_device(self, ws: str, device_id: str, /) -> Device: ...
    def list_devices(
        self, ws: str, /, *, offset: int = 0, limit: int = 50, q: str | None = None, department: str | None = None
    ) -> Page[Device]: ...
    def update_device(
        self, ws: str, device_id: str, payload: DeviceUpdate, /, *, expected_revision: int, actor_id: str
    ) -> Device: ...
    def delete_device(self, ws: str, device_id: str, /, *, expected_revision: int, actor_id: str) -> None: ...
    def put_binding(
        self,
        ws: str,
        device_id: str,
        scenario: Scenario,
        payload: BindingWrite,
        /,
        *,
        expected_revision: int | None,
        actor_id: str,
    ) -> Binding: ...
    def get_binding(self, ws: str, device_id: str, scenario: Scenario, /) -> Binding: ...
    def enqueue_run(
        self,
        ws: str,
        request_key: str,
        payload_hash: str,
        spec: RunSpec,
        input_snapshot: JsonObject | None,
        /,
        *,
        actor_id: str,
    ) -> Run: ...
    def get_run(self, ws: str, run_id: str, /) -> Run: ...
    def find_run_by_request_key(self, ws: str, request_key: str, /) -> Run | None: ...
    def list_runs(
        self, ws: str, device_id: str, scenario: Scenario, /, *, offset: int = 0, limit: int = 50
    ) -> Page[Run]: ...
    def claim_run(self, ws: str, run_id: str, nonce: str, /, *, actor_id: str) -> Run: ...
    def mark_dispatched(self, ws: str, run_id: str, nonce: str, dify_run_id: str, /, *, actor_id: str) -> Run: ...
    def mark_uncertain(self, ws: str, run_id: str, nonce: str, reason_code: str, /, *, actor_id: str) -> Run: ...
    def capture_input(self, ws: str, run_id: str, nonce: str, snapshot: JsonObject, /, *, actor_id: str) -> Run: ...
    def complete_run(
        self, ws: str, run_id: str, nonce: str, result: BusinessResult, result_digest: str, /, *, actor_id: str
    ) -> Run: ...
    def fail_run(self, ws: str, run_id: str, nonce: str, reason_code: str, /, *, actor_id: str) -> Run: ...
    def list_events(self, ws: str, run_id: str, /, *, after_sequence: int = 0) -> tuple[AuditEvent, ...]: ...
