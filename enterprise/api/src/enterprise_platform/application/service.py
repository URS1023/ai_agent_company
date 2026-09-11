"""Business commands shared by the portal and future Agent tools.

Execution requests freeze a published binding; neither request parameters nor a
model can choose a different tenant, actor, workflow revision or source credential.
"""

from pydantic import Field, TypeAdapter, field_validator

from .contracts import (
    Action,
    AuditEvent,
    Binding,
    BindingWrite,
    Contract,
    Device,
    DeviceCreate,
    DeviceUpdate,
    Identifier,
    JsonObject,
    Page,
    Principal,
    Run,
    RunSpec,
    Scenario,
    canonical_hash,
    validate_command_document,
)
from .errors import AccessDenied, Conflict, InvalidInput, NotFound
from .ports import EnterpriseRepository


class RunRequest(Contract):
    expected_binding_revision: int = Field(ge=1)
    parameters: JsonObject = Field(default_factory=dict)
    recompute_of: Identifier | None = None

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: JsonObject) -> JsonObject:
        return validate_command_document(value)


class BusinessService:
    repository: EnterpriseRepository

    def __init__(self, repository: EnterpriseRepository) -> None:
        self.repository = repository

    @staticmethod
    def require(principal: Principal, action: Action) -> None:
        if not principal.can(action):
            raise AccessDenied()

    def create_device(self, principal: Principal, payload: DeviceCreate) -> Device:
        self.require(principal, "manage")
        return self.repository.create_device(principal.workspace_id, payload, actor_id=principal.actor_id)

    def list_devices(
        self,
        principal: Principal,
        *,
        offset: int = 0,
        limit: int = 50,
        q: str | None = None,
        department: str | None = None,
    ) -> Page[Device]:
        self.require(principal, "read")
        if any(value is not None and (not isinstance(value, str) or len(value) > 200) for value in (q, department)):
            raise InvalidInput("invalid_device_filter")
        return self.repository.list_devices(
            principal.workspace_id,
            offset=offset,
            limit=limit,
            q=q.strip() or None if q is not None else None,
            department=department.strip() or None if department is not None else None,
        )

    def get_device(self, principal: Principal, device_id: str) -> Device:
        self.require(principal, "read")
        return self.repository.get_device(principal.workspace_id, device_id)

    def update_device(self, principal: Principal, device_id: str, payload: DeviceUpdate, revision: int) -> Device:
        self.require(principal, "manage")
        return self.repository.update_device(
            principal.workspace_id,
            device_id,
            payload,
            expected_revision=revision,
            actor_id=principal.actor_id,
        )

    def delete_device(self, principal: Principal, device_id: str, revision: int) -> None:
        self.require(principal, "manage")
        self.repository.delete_device(
            principal.workspace_id, device_id, expected_revision=revision, actor_id=principal.actor_id
        )

    def put_binding(
        self, principal: Principal, device_id: str, scenario: Scenario, payload: BindingWrite, revision: int | None
    ) -> Binding:
        self.require(principal, "manage")
        try:
            validate_command_document(payload.manifest)
        except ValueError:
            raise InvalidInput("invalid_binding_manifest") from None
        return self.repository.put_binding(
            principal.workspace_id,
            device_id,
            scenario,
            payload,
            expected_revision=revision,
            actor_id=principal.actor_id,
        )

    def get_binding(self, principal: Principal, device_id: str, scenario: Scenario) -> Binding:
        self.require(principal, "read")
        return self.repository.get_binding(principal.workspace_id, device_id, scenario)

    def enqueue(
        self, principal: Principal, device_id: str, scenario: Scenario, request_key: str, request: RunRequest
    ) -> Run:
        """Recover identical requests before consulting a mutable current binding.

        A late input capture does not change the original request identity. Recovery
        therefore compares the caller's fields with the frozen spec, not live input
        or the latest binding. A fresh key still goes through all current checks.
        """
        self.require(principal, "run")
        request_key = self._request_key(request_key)
        try:
            request = RunRequest.model_validate(request.model_dump())
        except ValueError:
            raise InvalidInput("invalid_run_parameters") from None
        existing = self._recover_request(principal, device_id, scenario, request_key, request)
        if existing is not None:
            return existing
        try:
            return self._enqueue_new(principal, device_id, scenario, request_key, request)
        except Conflict:
            # A concurrent insert may commit after lookup and before a binding/CAS check.
            existing = self._recover_request(principal, device_id, scenario, request_key, request)
            if existing is not None:
                return existing
            raise

    @staticmethod
    def _request_key(value: str) -> str:
        try:
            value = TypeAdapter(Identifier).validate_python(value)
            if value.startswith("scheduled-"):
                raise InvalidInput("reserved_schedule_request_key")
            return value
        except ValueError:
            raise InvalidInput("invalid_request_key") from None

    def _recover_request(
        self, principal: Principal, device_id: str, scenario: Scenario, request_key: str, request: RunRequest
    ) -> Run | None:
        existing = self.repository.find_run_by_request_key(principal.workspace_id, request_key)
        if existing is None:
            return None
        parameters = request.parameters
        if request.recompute_of is not None and not parameters:
            parameters = existing.spec.parameters
        matches = (
            existing.spec.device_id == device_id
            and existing.spec.scenario == scenario
            and existing.spec.binding_revision == request.expected_binding_revision
            and existing.spec.recompute_of == request.recompute_of
            and canonical_hash(existing.spec.parameters) == canonical_hash(parameters)
        )
        if not matches:
            raise Conflict("request_key_payload_conflict")
        return existing

    def _enqueue_new(
        self, principal: Principal, device_id: str, scenario: Scenario, request_key: str, request: RunRequest
    ) -> Run:
        binding = self.repository.get_binding(principal.workspace_id, device_id, scenario)
        if binding.revision != request.expected_binding_revision:
            raise Conflict("binding_revision_changed")
        allowed = binding.manifest.get("input_keys", [])
        if not isinstance(allowed, list) or any(not isinstance(key, str) for key in allowed):
            raise InvalidInput("invalid_binding_input_manifest")
        parameters = request.parameters
        snapshot: JsonObject | None = None
        if request.recompute_of:
            original = self.repository.get_run(principal.workspace_id, request.recompute_of)
            if original.spec.device_id != device_id or original.spec.scenario != scenario:
                raise InvalidInput("recompute_scope_mismatch")
            read_identity = ("source_id", "source_revision", "read_id", "read_revision")
            if any(getattr(original.spec, field) != getattr(binding, field) for field in read_identity):
                raise InvalidInput("recompute_read_identity_mismatch")
            if original.input_snapshot is None:
                raise InvalidInput("recompute_input_unavailable")
            if parameters and canonical_hash(parameters) != canonical_hash(original.spec.parameters):
                raise InvalidInput("recompute_parameters_mismatch")
            parameters = original.spec.parameters
            snapshot = original.input_snapshot
        if set(parameters) - set(allowed) or any(key.startswith("enterprise_") for key in parameters):
            raise InvalidInput("unregistered_run_parameters")
        try:
            spec = RunSpec.model_validate(
                {
                    **RunSpec.from_binding(binding).model_dump(),
                    "parameters": parameters,
                    "recompute_of": request.recompute_of,
                }
            )
        except ValueError:
            raise InvalidInput("invalid_run_parameters") from None
        payload_hash = canonical_hash({"spec": spec.model_dump(mode="json"), "input_snapshot": snapshot})
        return self.repository.enqueue_run(
            principal.workspace_id,
            request_key,
            payload_hash,
            spec,
            snapshot,
            actor_id=principal.actor_id,
        )

    def get_run(self, principal: Principal, run_id: str) -> Run:
        self.require(principal, "read")
        return self.repository.get_run(principal.workspace_id, run_id)

    def get_run_by_request_key(self, principal: Principal, request_key: str) -> Run:
        self.require(principal, "read")
        run = self.repository.find_run_by_request_key(principal.workspace_id, self._request_key(request_key))
        if run is None:
            raise NotFound("run_request_not_found")
        return run

    def list_runs(
        self, principal: Principal, device_id: str, scenario: Scenario, *, offset: int = 0, limit: int = 50
    ) -> Page[Run]:
        self.require(principal, "read")
        return self.repository.list_runs(principal.workspace_id, device_id, scenario, offset=offset, limit=limit)

    def list_events(self, principal: Principal, run_id: str, *, after_sequence: int = 0) -> tuple[AuditEvent, ...]:
        self.require(principal, "read")
        return self.repository.list_events(principal.workspace_id, run_id, after_sequence=after_sequence)
