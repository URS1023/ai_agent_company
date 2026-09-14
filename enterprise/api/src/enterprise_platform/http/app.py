"""Authenticated business API with injected persistence and native identity.

This factory does not start a server or create database tables. Deployment composition
must provide an independently migrated enterprise repository and a real identity adapter.
OpenAPI describes the native session/CSRF contract without installing another verifier.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, Field

from enterprise_platform.adapters.workbench_dispatch import WorkbenchChatDispatcher
from enterprise_platform.application.contracts import (
    AuditEvent,
    Binding,
    BindingWrite,
    BusinessResult,
    Contract,
    Device,
    DeviceCreate,
    DeviceUpdate,
    JsonObject,
    Page,
    Principal,
    Run,
    RunStatus,
    Scenario,
    canonical_hash,
)
from enterprise_platform.application.dashboard_service import DashboardService
from enterprise_platform.application.errors import AccessDenied, EnterpriseError, InvalidInput
from enterprise_platform.application.office_directory import OfficeDirectoryService
from enterprise_platform.application.office_edits import OfficeEditService, OfficeFileCreator
from enterprise_platform.application.office_export import OfficeExportService
from enterprise_platform.application.ports import IdentityProvider
from enterprise_platform.application.schedule_service import ScheduleService
from enterprise_platform.application.service import BusinessService, RunRequest
from enterprise_platform.application.source_ports import SourceManagement
from enterprise_platform.application.workbench_branch_service import WorkbenchBranchService
from enterprise_platform.application.workflow_activation_service import WorkflowActivationService
from enterprise_platform.application.workflow_enrollment_service import WorkflowEnrollmentService
from enterprise_platform.application.workflow_provisioning_service import WorkflowProvisioningService
from enterprise_platform.application.workflow_setup_service import WorkflowSetupService
from enterprise_platform.http.dashboard_routes import add_dashboard_routes
from enterprise_platform.http.office_routes import add_office_routes
from enterprise_platform.http.schedule_routes import add_schedule_routes
from enterprise_platform.http.source_routes import add_source_routes
from enterprise_platform.http.workbench_routes import add_workbench_routes
from enterprise_platform.http.workflow_activation_routes import add_workflow_activation_routes
from enterprise_platform.http.workflow_enrollment_routes import add_workflow_enrollment_routes
from enterprise_platform.http.workflow_provisioning_routes import add_workflow_provisioning_routes
from enterprise_platform.http.workflow_setup_routes import add_workflow_setup_routes

PREFIX = "/enterprise/api/v1"
ETAG_HEADER = {
    "ETag": {
        "description": "Quoted device revision; send it unchanged as If-Match on update or deletion.",
        "schema": {"type": "string", "pattern": r'^"[1-9][0-9]*"$'},
    }
}


class ErrorResponse(Contract):
    """Stable public failure shape; driver, request and credential details stay private."""

    code: str


class BusinessPermissions(Contract):
    read: bool
    manage: bool
    run: bool
    review: bool


class BusinessAccess(Contract):
    actor_id: str
    workspace_id: str
    display_name: str
    permissions: BusinessPermissions


class BindingCommand(Contract):
    binding: BindingWrite
    expected_revision: int | None = Field(default=None, ge=1)


class RunView(Contract):
    id: str
    device_id: str
    scenario: Scenario
    binding_revision: int
    specification_revision: str
    status: RunStatus
    result: BusinessResult | None
    reason_code: str | None
    has_input_snapshot: bool
    input_snapshot_digest: str | None
    parameters: JsonObject
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_run(cls, run: Run) -> "RunView":
        return cls(
            id=run.id,
            device_id=run.spec.device_id,
            scenario=run.spec.scenario,
            binding_revision=run.spec.binding_revision,
            specification_revision=run.spec.specification_revision,
            status=run.status,
            result=run.result,
            reason_code=run.reason_code,
            has_input_snapshot=run.input_snapshot is not None,
            input_snapshot_digest=canonical_hash(run.input_snapshot) if run.input_snapshot is not None else None,
            parameters=run.spec.parameters,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )


class PreconditionRequired(EnterpriseError):
    code = "revision_precondition_required"
    status_code = 428


def parse_revision(value: str | None) -> int:
    if value is None or re.fullmatch(r'"?[1-9][0-9]{0,8}"?', value) is None:
        raise PreconditionRequired()
    if value.startswith('"') != value.endswith('"'):
        raise PreconditionRequired()
    return int(value.strip('"'))


class EnterpriseAPI(FastAPI):
    """Document AND/OR auth requirements while leaving verification with IdentityProvider.

    FastAPI's independent Security dependencies describe OR alternatives, which would
    incorrectly make CSRF optional here. A schema-only overlay expresses the native
    combination and preserves the existing 401/403/428 runtime error semantics.
    """

    allowed_origins: tuple[str, ...] = ()

    def openapi(self) -> dict[str, object]:
        schema = super().openapi()
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        for name, cookie in [
            ("DifySession", "access_token"),
            ("DifyHostSession", "__Host-access_token"),
            ("DifyCSRFCookie", "csrf_token"),
            ("DifyHostCSRFCookie", "__Host-csrf_token"),
        ]:
            schemes[name] = {"type": "apiKey", "in": "cookie", "name": cookie}
        schemes["DifyBearer"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "Native Dify account session, not an application Service API key.",
        }
        schemes["DifyCSRFHeader"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-CSRF-Token",
            "description": "Required even on GET; must match a native CSRF cookie and pass Dify validation.",
        }
        requirements: list[dict[str, list[str]]] = [
            {session: [], csrf_cookie: [], "DifyCSRFHeader": []}
            for session in ["DifySession", "DifyHostSession", "DifyBearer"]
            for csrf_cookie in ["DifyCSRFCookie", "DifyHostCSRFCookie"]
        ]
        for path, operations in schema["paths"].items():
            if not path.startswith(f"{PREFIX}/"):
                continue
            for method, operation in operations.items():
                if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                operation["security"] = requirements
                parameters = operation.setdefault("parameters", [])
                for parameter in parameters:
                    if parameter.get("in") == "header" and parameter["name"].lower() == "if-match":
                        parameter.update(
                            required=True,
                            schema={"type": "string", "pattern": r'^(?:[1-9][0-9]{0,8}|"[1-9][0-9]{0,8}")$'},
                            description="Current resource revision, preferably its quoted ETag. Missing/invalid: 428.",
                        )
                if method not in {"get", "head", "options"}:
                    origin_schema: dict[str, object] = {"type": "string"}
                    if self.allowed_origins:
                        origin_schema["enum"] = list(self.allowed_origins)
                    operation["parameters"] = [
                        parameter for parameter in parameters if parameter["name"].lower() != "origin"
                    ] + [
                        {
                            "name": "Origin",
                            "in": "header",
                            "required": True,
                            "schema": origin_schema,
                            "description": (
                                "Browser-supplied origin must exactly match a configured allowed origin. "
                                "Missing/untrusted: 403. An empty deployment allowlist disables writes."
                            ),
                        }
                    ]
        return schema


def create_app(
    service: BusinessService,
    identity: IdentityProvider,
    *,
    allowed_origins: tuple[str, ...] = (),
    sources: SourceManagement | None = None,
    workflow_setups: WorkflowSetupService | None = None,
    workflow_provisioning: WorkflowProvisioningService | None = None,
    workflow_enrollment: WorkflowEnrollmentService | None = None,
    workflow_activation: WorkflowActivationService | None = None,
    schedules: ScheduleService | None = None,
    dashboards: DashboardService | None = None,
    workbench: WorkbenchChatDispatcher | None = None,
    workbench_branches: WorkbenchBranchService | None = None,
    office_exports: OfficeExportService | None = None,
    office_files: OfficeEditService | None = None,
    office_directory: OfficeDirectoryService | None = None,
    office_creator: OfficeFileCreator | None = None,
) -> FastAPI:
    if "*" in allowed_origins:
        raise ValueError("Explicit browser origins are required")
    app = EnterpriseAPI(
        title="Enterprise business API",
        version="0.2.0",
        docs_url="/enterprise/api/docs",
        redoc_url=None,
        openapi_url="/enterprise/api/openapi.json",
    )
    app.allowed_origins = allowed_origins
    router = APIRouter(
        responses={
            401: {"model": ErrorResponse, "description": "Native Dify account session is not authenticated."},
            403: {"model": ErrorResponse, "description": "Identity, role, CSRF or browser origin check failed."},
            404: {"model": ErrorResponse, "description": "Resource does not exist in the current workspace."},
            409: {"model": ErrorResponse, "description": "Revision, idempotency key or resource state conflict."},
            422: {"model": ErrorResponse, "description": "Invalid request or business command."},
            500: {"model": ErrorResponse, "description": "Enterprise domain operation failed."},
            503: {"model": ErrorResponse, "description": "Native identity or persistence dependency unavailable."},
        }
    )

    @app.exception_handler(EnterpriseError)
    async def handle_business_error(request: Request, error: EnterpriseError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content=ErrorResponse(code=error.code).model_dump())

    @app.exception_handler(RequestValidationError)
    async def handle_request_error(request: Request, error: RequestValidationError) -> JSONResponse:
        # FastAPI's default detail includes rejected input, which may contain credentials.
        return JSONResponse(status_code=422, content=ErrorResponse(code=InvalidInput.code).model_dump())

    async def actor(request: Request) -> Principal:
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") not in allowed_origins:
            raise AccessDenied("untrusted_origin")
        return await identity.resolve(
            cookie_header=request.headers.get("cookie"),
            authorization=request.headers.get("authorization"),
            csrf_token=request.headers.get("x-csrf-token"),
        )

    @app.get("/enterprise/api/health")
    def health() -> dict[str, str]:
        return {"status": "alive"}

    @router.get(f"{PREFIX}/me")
    def current_access(response: Response, principal: Annotated[Principal, Depends(actor)]) -> BusinessAccess:
        response.headers["Cache-Control"] = "private, no-store"
        return BusinessAccess(
            actor_id=principal.actor_id,
            workspace_id=principal.workspace_id,
            display_name=principal.display_name,
            permissions=BusinessPermissions(
                read=principal.can("read"),
                manage=principal.can("manage"),
                run=principal.can("run"),
                review=principal.can("review"),
            ),
        )

    @router.get(f"{PREFIX}/devices")
    def list_devices(
        principal: Annotated[Principal, Depends(actor)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        q: Annotated[str | None, Query(max_length=200)] = None,
        department: Annotated[str | None, Query(max_length=200)] = None,
    ) -> Page[Device]:
        return service.list_devices(principal, offset=offset, limit=limit, q=q, department=department)

    @router.post(f"{PREFIX}/devices", status_code=201, responses={201: {"headers": ETAG_HEADER}})
    def create_device(
        payload: DeviceCreate, response: Response, principal: Annotated[Principal, Depends(actor)]
    ) -> Device:
        device = service.create_device(principal, payload)
        response.headers["ETag"] = f'"{device.revision}"'
        return device

    @router.get(f"{PREFIX}/devices/{{device_id}}", responses={200: {"headers": ETAG_HEADER}})
    def get_device(device_id: str, response: Response, principal: Annotated[Principal, Depends(actor)]) -> Device:
        device = service.get_device(principal, device_id)
        response.headers["ETag"] = f'"{device.revision}"'
        return device

    @router.put(
        f"{PREFIX}/devices/{{device_id}}",
        responses={
            200: {"headers": ETAG_HEADER},
            428: {"model": ErrorResponse, "description": "A valid current device revision is required in If-Match."},
        },
    )
    def update_device(
        device_id: str,
        payload: DeviceUpdate,
        response: Response,
        principal: Annotated[Principal, Depends(actor)],
        if_match: Annotated[str | None, Header()] = None,
    ) -> Device:
        device = service.update_device(principal, device_id, payload, parse_revision(if_match))
        response.headers["ETag"] = f'"{device.revision}"'
        return device

    @router.delete(
        f"{PREFIX}/devices/{{device_id}}",
        status_code=204,
        responses={428: {"model": ErrorResponse, "description": "A valid device revision is required in If-Match."}},
    )
    def delete_device(
        device_id: str,
        principal: Annotated[Principal, Depends(actor)],
        if_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        service.delete_device(principal, device_id, parse_revision(if_match))
        return Response(status_code=204)

    @router.get(f"{PREFIX}/devices/{{device_id}}/bindings/{{scenario}}")
    def get_binding(device_id: str, scenario: Scenario, principal: Annotated[Principal, Depends(actor)]) -> Binding:
        return service.get_binding(principal, device_id, scenario)

    @router.put(f"{PREFIX}/devices/{{device_id}}/bindings/{{scenario}}")
    def put_binding(
        device_id: str, scenario: Scenario, command: BindingCommand, principal: Annotated[Principal, Depends(actor)]
    ) -> Binding:
        return service.put_binding(principal, device_id, scenario, command.binding, command.expected_revision)

    @router.post(f"{PREFIX}/devices/{{device_id}}/bindings/{{scenario}}/runs", status_code=202)
    def enqueue_run(
        device_id: str,
        scenario: Scenario,
        payload: RunRequest,
        principal: Annotated[Principal, Depends(actor)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ) -> RunView:
        return RunView.from_run(service.enqueue(principal, device_id, scenario, idempotency_key, payload))

    @router.get(f"{PREFIX}/devices/{{device_id}}/bindings/{{scenario}}/runs")
    def list_runs(
        device_id: str,
        scenario: Scenario,
        principal: Annotated[Principal, Depends(actor)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> Page[RunView]:
        page = service.list_runs(principal, device_id, scenario, offset=offset, limit=limit)
        return Page[RunView](
            items=tuple(RunView.from_run(run) for run in page.items),
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )

    @router.get(f"{PREFIX}/runs/{{run_id}}")
    def get_run(run_id: str, principal: Annotated[Principal, Depends(actor)]) -> RunView:
        return RunView.from_run(service.get_run(principal, run_id))

    @router.get(f"{PREFIX}/runs/{{run_id}}/events")
    def events(
        run_id: str,
        principal: Annotated[Principal, Depends(actor)],
        after_sequence: Annotated[int, Query(ge=0)] = 0,
    ) -> tuple[AuditEvent, ...]:
        return service.list_events(principal, run_id, after_sequence=after_sequence)

    @router.get(f"{PREFIX}/run-requests/lookup")
    def get_run_by_request_key(
        response: Response,
        principal: Annotated[Principal, Depends(actor)],
        request_key: Annotated[str, Query(min_length=1, max_length=128)],
    ) -> RunView:
        response.headers["Cache-Control"] = "private, no-store"
        return RunView.from_run(service.get_run_by_request_key(principal, request_key))

    add_source_routes(router, sources, actor, parse_revision, ErrorResponse)
    add_workflow_setup_routes(router, workflow_setups, actor, ErrorResponse)
    add_workflow_provisioning_routes(router, workflow_provisioning, actor, ErrorResponse)
    add_workflow_enrollment_routes(router, workflow_enrollment, actor, ErrorResponse)
    add_workflow_activation_routes(router, workflow_activation, actor, ErrorResponse)
    add_schedule_routes(router, schedules, actor, ErrorResponse)
    add_dashboard_routes(router, dashboards, actor, ErrorResponse)
    add_workbench_routes(router, workbench, actor, ErrorResponse, workbench_branches)
    add_office_routes(router, office_exports, actor, office_files, office_directory, office_creator)
    app.include_router(router)
    return app
