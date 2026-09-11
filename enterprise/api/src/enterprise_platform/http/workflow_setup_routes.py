"""Native-authenticated draft setup routes with private, sanitized responses.

The host identity dependency retains native Origin/CSRF checks. Session headers
are ephemeral importer input, created only after that dependency has succeeded.
"""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from enterprise_platform.application.contracts import Page, Principal, Scenario
from enterprise_platform.application.errors import DependencyUnavailable, EnterpriseError, InvalidInput
from enterprise_platform.application.workflow_setup_contracts import SetupRequest, SetupView
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession
from enterprise_platform.application.workflow_setup_service import WorkflowSetupService


def add_workflow_setup_routes(
    router: APIRouter,
    setup_service: WorkflowSetupService | None,
    actor: Callable[[Request], Awaitable[Principal]],
    error_model: type[BaseModel],
) -> None:
    class PrivateSetupRoute(APIRoute):
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
        route_class=PrivateSetupRoute,
        responses={code: {"model": error_model} for code in (401, 403, 404, 409, 422, 503)},
    )
    collection = "/enterprise/api/v1/devices/{device_id}/bindings/{scenario}/workflow-setups"

    def service() -> WorkflowSetupService:
        if setup_service is None:
            raise DependencyUnavailable("workflow_setup_unavailable")
        return setup_service

    @routes.post(collection, status_code=201)
    async def start_setup(
        request: Request,
        payload: SetupRequest,
        device_id: Annotated[str, Path(min_length=1, max_length=128)],
        scenario: Scenario,
        principal: Annotated[Principal, Depends(actor)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ) -> SetupView:
        active = service()
        session = NativeSetupSession(
            cookie_header=request.headers.get("cookie"),
            authorization=request.headers.get("authorization"),
            csrf_token=request.headers.get("x-csrf-token"),
        )
        return await active.start(principal, session, device_id, scenario, payload, idempotency_key)

    @routes.get(collection)
    async def list_setups(
        device_id: Annotated[str, Path(min_length=1, max_length=128)],
        scenario: Scenario,
        principal: Annotated[Principal, Depends(actor)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> Page[SetupView]:
        return await service().list(principal, device_id, scenario, offset=offset, limit=limit)

    @routes.get("/enterprise/api/v1/workflow-setups/{setup_id}")
    async def get_setup(
        setup_id: Annotated[str, Path(min_length=1, max_length=128)],
        principal: Annotated[Principal, Depends(actor)],
    ) -> SetupView:
        return await service().get(principal, setup_id)

    router.include_router(routes)
