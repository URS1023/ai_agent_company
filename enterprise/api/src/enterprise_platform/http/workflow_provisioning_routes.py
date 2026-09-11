"""Private provisioning commands; native session headers exist only during advance."""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from enterprise_platform.application.contracts import Contract, Identifier, Page, Principal
from enterprise_platform.application.errors import DependencyUnavailable, EnterpriseError, InvalidInput
from enterprise_platform.application.workflow_plugin_profiles import PluginProfileChoice
from enterprise_platform.application.workflow_provisioning_contracts import NativeUUID, ProvisioningView, Revision
from enterprise_platform.application.workflow_provisioning_service import WorkflowProvisioningService
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession


class ProvisioningStartRequest(Contract):
    config_ref: Identifier
    config_revision: Revision


class ProvisioningAdvanceRequest(Contract):
    expected_revision: Revision


def add_workflow_provisioning_routes(
    router: APIRouter,
    provisioning_service: WorkflowProvisioningService | None,
    actor: Callable[[Request], Awaitable[Principal]],
    error_model: type[BaseModel],
) -> None:
    class PrivateProvisioningRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[None, None, Response]]:
            handler = super().get_route_handler()

            async def private_handler(request: Request) -> Response:
                try:
                    response = await handler(request)
                except EnterpriseError as error:
                    response = JSONResponse(status_code=error.status_code, content={"code": error.code})
                except RequestValidationError:
                    response = JSONResponse(status_code=422, content={"code": InvalidInput.code})
                response.headers["Cache-Control"] = "private, no-store"
                return response

            return private_handler

    routes = APIRouter(
        route_class=PrivateProvisioningRoute,
        responses={code: {"model": error_model} for code in (401, 403, 404, 409, 422, 503)},
    )

    def service() -> WorkflowProvisioningService:
        if provisioning_service is None:
            raise DependencyUnavailable("workflow_provisioning_unavailable")
        return provisioning_service

    @routes.post("/enterprise/api/v1/workflow-setups/{setup_id}/provisioning", status_code=201)
    async def start_provisioning(
        payload: ProvisioningStartRequest,
        setup_id: Annotated[str, Path(min_length=1, max_length=128)],
        principal: Annotated[Principal, Depends(actor)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ) -> ProvisioningView:
        return await service().start(
            principal,
            setup_id,
            config_ref=payload.config_ref,
            config_revision=payload.config_revision,
            request_key=idempotency_key,
        )

    @routes.get("/enterprise/api/v1/workflow-provisioning/{provisioning_id}")
    async def get_provisioning(
        provisioning_id: NativeUUID,
        principal: Annotated[Principal, Depends(actor)],
    ) -> ProvisioningView:
        return await service().get(principal, provisioning_id)

    @routes.post("/enterprise/api/v1/workflow-provisioning/{provisioning_id}/advance")
    async def advance_provisioning(
        request: Request,
        payload: ProvisioningAdvanceRequest,
        provisioning_id: NativeUUID,
        principal: Annotated[Principal, Depends(actor)],
    ) -> ProvisioningView:
        active = service()
        session = NativeSetupSession(
            cookie_header=request.headers.get("cookie"),
            authorization=request.headers.get("authorization"),
            csrf_token=request.headers.get("x-csrf-token"),
        )
        return await active.advance(principal, session, provisioning_id, expected_revision=payload.expected_revision)

    @routes.get("/enterprise/api/v1/workflow-setups/{setup_id}/provisioning")
    async def list_provisioning(
        setup_id: Annotated[str, Path(min_length=1, max_length=128)],
        principal: Annotated[Principal, Depends(actor)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> Page[ProvisioningView]:
        return await service().list(principal, setup_id, offset=offset, limit=limit)

    @routes.get("/enterprise/api/v1/workflow-setups/{setup_id}/provisioning-profiles")
    async def list_provisioning_profiles(
        setup_id: Annotated[str, Path(min_length=1, max_length=128)],
        principal: Annotated[Principal, Depends(actor)],
    ) -> tuple[PluginProfileChoice, ...]:
        return await service().list_profiles(principal, setup_id)

    router.include_router(routes)
