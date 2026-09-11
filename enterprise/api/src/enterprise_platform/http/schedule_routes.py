"""Private schedule management routes; no browser tick/poll/identity override."""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from enterprise_platform.application.contracts import Identifier, Principal, Scenario
from enterprise_platform.application.errors import DependencyUnavailable, EnterpriseError, InvalidInput
from enterprise_platform.application.schedule_service import (
    CreateSchedule,
    ScheduleActorChoice,
    ScheduleService,
    ScheduleSwitch,
)
from enterprise_platform.application.scheduling import IntervalSchedule


def add_schedule_routes(
    router: APIRouter,
    schedules: ScheduleService | None,
    actor: Callable[[Request], Awaitable[Principal]],
    error_model: type[BaseModel],
) -> None:
    class PrivateScheduleRoute(APIRoute):
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
        route_class=PrivateScheduleRoute,
        responses={code: {"model": error_model} for code in (401, 403, 404, 409, 422, 503)},
    )

    def service() -> ScheduleService:
        if schedules is None:
            raise DependencyUnavailable("scheduling_unavailable")
        return schedules

    @routes.get("/enterprise/api/v1/devices/{device_id}/{scenario}/schedule/actors")
    async def schedule_actor_choices(
        device_id: Identifier,
        scenario: Scenario,
        principal: Annotated[Principal, Depends(actor)],
    ) -> tuple[ScheduleActorChoice, ...]:
        return await service().actor_choices(principal, device_id, scenario)

    @routes.get("/enterprise/api/v1/devices/{device_id}/{scenario}/schedule")
    async def find_device_schedule(
        device_id: Identifier,
        scenario: Scenario,
        principal: Annotated[Principal, Depends(actor)],
    ) -> IntervalSchedule | None:
        return await service().find_for_device(principal, device_id, scenario)

    @routes.post("/enterprise/api/v1/devices/{device_id}/{scenario}/schedule", status_code=201)
    async def create_schedule(
        device_id: Identifier,
        scenario: Scenario,
        payload: CreateSchedule,
        principal: Annotated[Principal, Depends(actor)],
    ) -> IntervalSchedule:
        return await service().create(principal, device_id, scenario, payload)

    @routes.get("/enterprise/api/v1/schedules/{schedule_id}")
    async def get_schedule(
        schedule_id: Identifier, principal: Annotated[Principal, Depends(actor)]
    ) -> IntervalSchedule:
        return await service().get(principal, schedule_id)

    @routes.post("/enterprise/api/v1/schedules/{schedule_id}/state")
    async def switch_schedule(
        schedule_id: Identifier,
        payload: ScheduleSwitch,
        principal: Annotated[Principal, Depends(actor)],
    ) -> IntervalSchedule:
        return await service().set_enabled(
            principal,
            schedule_id,
            expected_revision=payload.expected_revision,
            enabled=payload.enabled,
        )

    router.include_router(routes)
