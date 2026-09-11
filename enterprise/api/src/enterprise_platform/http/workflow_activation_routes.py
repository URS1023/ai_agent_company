"""Private activation endpoints; identity credentials never enter activation commands."""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from enterprise_platform.application.contracts import Contract, Identifier, Principal
from enterprise_platform.application.errors import DependencyUnavailable, EnterpriseError, InvalidInput
from enterprise_platform.application.workflow_activation_contracts import ActivationView
from enterprise_platform.application.workflow_activation_service import WorkflowActivationService
from enterprise_platform.application.workflow_provisioning_contracts import NativeUUID, Revision


class ActivationStartRequest(Contract):
    specification_revision: Identifier


class ActivationRevokeRequest(Contract):
    expected_revision: Revision


class ActivationSpecificationChoices(Contract):
    items: tuple[Identifier, ...]


def add_workflow_activation_routes(
    router: APIRouter,
    activation_service: WorkflowActivationService | None,
    actor: Callable[[Request], Awaitable[Principal]],
    error_model: type[BaseModel],
) -> None:
    class PrivateActivationRoute(APIRoute):
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
        route_class=PrivateActivationRoute,
        responses={code: {"model": error_model} for code in (401, 403, 404, 409, 422, 503)},
    )

    def service() -> WorkflowActivationService:
        if activation_service is None:
            raise DependencyUnavailable("workflow_activation_unavailable")
        return activation_service

    @routes.get("/enterprise/api/v1/workflow-enrollments/{enrollment_id}/activation-specifications")
    async def activation_specifications(
        enrollment_id: NativeUUID, principal: Annotated[Principal, Depends(actor)]
    ) -> ActivationSpecificationChoices:
        return ActivationSpecificationChoices(items=await service().specifications(principal, enrollment_id))

    @routes.post("/enterprise/api/v1/workflow-enrollments/{enrollment_id}/activation", status_code=201)
    async def start_activation(
        payload: ActivationStartRequest, enrollment_id: NativeUUID, principal: Annotated[Principal, Depends(actor)]
    ) -> ActivationView:
        return await service().start(principal, enrollment_id, specification_revision=payload.specification_revision)

    @routes.get("/enterprise/api/v1/workflow-activations/{activation_id}")
    async def get_activation(
        activation_id: NativeUUID, principal: Annotated[Principal, Depends(actor)]
    ) -> ActivationView:
        return await service().get(principal, activation_id)

    @routes.get("/enterprise/api/v1/workflow-enrollments/{enrollment_id}/activation")
    async def find_activation(
        enrollment_id: NativeUUID,
        principal: Annotated[Principal, Depends(actor)],
    ) -> ActivationView | None:
        return await service().find(principal, enrollment_id)

    @routes.post("/enterprise/api/v1/workflow-activations/{activation_id}/revoke")
    async def revoke_activation(
        payload: ActivationRevokeRequest,
        activation_id: NativeUUID,
        principal: Annotated[Principal, Depends(actor)],
    ) -> ActivationView:
        return await service().revoke(principal, activation_id, expected_revision=payload.expected_revision)

    router.include_router(routes)
