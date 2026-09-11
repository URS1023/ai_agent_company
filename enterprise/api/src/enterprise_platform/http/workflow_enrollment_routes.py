"""Private enrollment endpoints; only advance receives ephemeral native session headers."""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from enterprise_platform.application.contracts import Contract, Principal
from enterprise_platform.application.errors import DependencyUnavailable, EnterpriseError, InvalidInput
from enterprise_platform.application.workflow_enrollment_contracts import EnrollmentView
from enterprise_platform.application.workflow_enrollment_service import WorkflowEnrollmentService
from enterprise_platform.application.workflow_provisioning_contracts import NativeUUID, Revision
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession


class EnrollmentStartRequest(Contract):
    pass


class EnrollmentAdvanceRequest(Contract):
    expected_revision: Revision


def add_workflow_enrollment_routes(
    router: APIRouter,
    enrollment_service: WorkflowEnrollmentService | None,
    actor: Callable[[Request], Awaitable[Principal]],
    error_model: type[BaseModel],
) -> None:
    class PrivateEnrollmentRoute(APIRoute):
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
        route_class=PrivateEnrollmentRoute,
        responses={code: {"model": error_model} for code in (401, 403, 404, 409, 422, 503)},
    )

    def service() -> WorkflowEnrollmentService:
        if enrollment_service is None:
            raise DependencyUnavailable("workflow_enrollment_unavailable")
        return enrollment_service

    @routes.post("/enterprise/api/v1/workflow-provisioning/{provisioning_id}/enrollment", status_code=201)
    async def start_enrollment(
        payload: EnrollmentStartRequest, provisioning_id: NativeUUID, principal: Annotated[Principal, Depends(actor)]
    ) -> EnrollmentView:
        return await service().start(principal, provisioning_id)

    @routes.get("/enterprise/api/v1/workflow-enrollments/{enrollment_id}")
    async def get_enrollment(
        enrollment_id: NativeUUID, principal: Annotated[Principal, Depends(actor)]
    ) -> EnrollmentView:
        return await service().get(principal, enrollment_id)

    @routes.get("/enterprise/api/v1/workflow-provisioning/{provisioning_id}/enrollment")
    async def find_enrollment(
        provisioning_id: NativeUUID,
        principal: Annotated[Principal, Depends(actor)],
    ) -> EnrollmentView | None:
        return await service().find(principal, provisioning_id)

    @routes.post("/enterprise/api/v1/workflow-enrollments/{enrollment_id}/advance")
    async def advance_enrollment(
        request: Request,
        payload: EnrollmentAdvanceRequest,
        enrollment_id: NativeUUID,
        principal: Annotated[Principal, Depends(actor)],
    ) -> EnrollmentView:
        active = service()
        session = NativeSetupSession(
            cookie_header=request.headers.get("cookie"),
            authorization=request.headers.get("authorization"),
            csrf_token=request.headers.get("x-csrf-token"),
        )
        return await active.advance(principal, session, enrollment_id, expected_revision=payload.expected_revision)

    router.include_router(routes)
