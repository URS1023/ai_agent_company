"""Private dashboard routes; saved SQL executes only via an explicitly authorized trial POST."""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from enterprise_platform.application.contracts import Identifier, Principal
from enterprise_platform.application.dashboard_contracts import (
    DashboardBindingView,
    DashboardBindingWrite,
    DashboardCreateCommand,
    DashboardPage,
    DashboardQueryCatalog,
    DashboardRefreshCommand,
    DashboardTemplateCatalog,
    DashboardView,
)
from enterprise_platform.application.dashboard_service import DashboardService
from enterprise_platform.application.dashboard_sql_generation import (
    GenerateSqlCommand,
    SqlDraftInspection,
    SqlGenerationReceipt,
)
from enterprise_platform.application.dashboard_sql_sample_policy import SqlPolicySampleCheck
from enterprise_platform.application.dashboard_sql_trial import SqlTrialCommand
from enterprise_platform.application.dashboard_sql_trial_assessment import SqlTrialAssessment
from enterprise_platform.application.dashboard_sql_trial_evidence import SqlTrialEvidence, SqlTrialPage
from enterprise_platform.application.dashboard_sql_trial_preview import (
    SqlDraftPreview,
    SqlDraftPreviewSelection,
    SqlTrialPreview,
)
from enterprise_platform.application.dashboard_sql_trial_result import SqlTrialResult
from enterprise_platform.application.errors import DependencyUnavailable, EnterpriseError, InvalidInput


def add_dashboard_routes(
    router: APIRouter,
    dashboards: DashboardService | None,
    actor: Callable[[Request], Awaitable[Principal]],
    error_model: type[BaseModel],
) -> None:
    class PrivateDashboardRoute(APIRoute):
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
        route_class=PrivateDashboardRoute,
        responses={code: {"model": error_model} for code in (401, 403, 404, 409, 422, 503)},
    )

    def service() -> DashboardService:
        if dashboards is None:
            raise DependencyUnavailable("dashboards_unavailable")
        return dashboards

    @routes.get("/enterprise/api/v1/dashboard-queries")
    async def list_dashboard_queries(principal: Annotated[Principal, Depends(actor)]) -> DashboardQueryCatalog:
        return await service().queries(principal)

    @routes.get("/enterprise/api/v1/dashboard-templates")
    async def list_dashboard_templates(principal: Annotated[Principal, Depends(actor)]) -> DashboardTemplateCatalog:
        return await service().templates(principal)

    @routes.post("/enterprise/api/v1/dashboards")
    async def create_dashboard(
        payload: DashboardCreateCommand,
        principal: Annotated[Principal, Depends(actor)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ) -> DashboardView:
        return await service().create(principal, payload, request_key=idempotency_key)

    @routes.get("/enterprise/api/v1/dashboards")
    async def list_dashboards(
        principal: Annotated[Principal, Depends(actor)],
        after: Identifier | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> DashboardPage:
        return await service().list(principal, after=after, limit=limit)

    @routes.get("/enterprise/api/v1/dashboards/{dashboard_id}")
    async def get_dashboard(dashboard_id: Identifier, principal: Annotated[Principal, Depends(actor)]) -> DashboardView:
        return await service().get(principal, dashboard_id)

    @routes.get("/enterprise/api/v1/dashboards/{dashboard_id}/bindings")
    async def get_bindings(
        dashboard_id: Identifier, principal: Annotated[Principal, Depends(actor)]
    ) -> DashboardBindingView:
        return await service().get_bindings(principal, dashboard_id)

    @routes.put("/enterprise/api/v1/dashboards/{dashboard_id}/bindings")
    async def save_bindings(
        dashboard_id: Identifier, payload: DashboardBindingWrite, principal: Annotated[Principal, Depends(actor)]
    ) -> DashboardBindingView:
        return await service().save_bindings(principal, dashboard_id, payload)

    @routes.post("/enterprise/api/v1/dashboards/{dashboard_id}/refresh")
    async def refresh_dashboard(
        dashboard_id: Identifier, payload: DashboardRefreshCommand, principal: Annotated[Principal, Depends(actor)]
    ) -> DashboardView:
        return await service().refresh(principal, dashboard_id, expected_revision=payload.expected_revision)

    @routes.post("/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals")
    async def generate_sql(
        dashboard_id: Identifier, payload: GenerateSqlCommand, principal: Annotated[Principal, Depends(actor)]
    ) -> SqlGenerationReceipt:
        return await service().generate_sql(principal, dashboard_id, payload)

    @routes.get("/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}")
    async def inspect_sql_draft(
        dashboard_id: Identifier, draft_id: Identifier, principal: Annotated[Principal, Depends(actor)]
    ) -> SqlDraftInspection:
        return await service().inspect_sql_draft(principal, dashboard_id, draft_id)

    @routes.post("/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trial")
    async def trial_sql_draft(
        dashboard_id: Identifier,
        draft_id: Identifier,
        payload: SqlTrialCommand,
        principal: Annotated[Principal, Depends(actor)],
    ) -> SqlTrialResult:
        return await service().trial_sql(principal, dashboard_id, draft_id, payload)

    @routes.get("/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials/{trial_id}")
    async def inspect_sql_trial(
        dashboard_id: Identifier,
        draft_id: Identifier,
        trial_id: Identifier,
        principal: Annotated[Principal, Depends(actor)],
    ) -> SqlTrialEvidence:
        return await service().inspect_sql_trial(principal, dashboard_id, draft_id, trial_id)

    @routes.get("/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials")
    async def list_sql_trials(
        dashboard_id: Identifier,
        draft_id: Identifier,
        principal: Annotated[Principal, Depends(actor)],
        after: Identifier | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> SqlTrialPage:
        return await service().list_sql_trials(principal, dashboard_id, draft_id, after=after, limit=limit)

    @routes.get("/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials/{trial_id}/assessment")
    async def assess_sql_trial(
        dashboard_id: Identifier,
        draft_id: Identifier,
        trial_id: Identifier,
        principal: Annotated[Principal, Depends(actor)],
    ) -> SqlTrialAssessment:
        return await service().assess_sql_trial(principal, dashboard_id, draft_id, trial_id)

    @routes.get("/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials/{trial_id}/sample-check")
    async def check_sql_trial_policy(
        dashboard_id: Identifier,
        draft_id: Identifier,
        trial_id: Identifier,
        policy_id: Identifier,
        policy_revision: Identifier,
        principal: Annotated[Principal, Depends(actor)],
    ) -> SqlPolicySampleCheck:
        return await service().check_sql_trial_policy(
            principal, dashboard_id, draft_id, trial_id, policy_id, policy_revision
        )

    @routes.get(
        "/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials/{trial_id}/sample-check/automatic"
    )
    async def auto_check_sql_trial_policy(
        dashboard_id: Identifier,
        draft_id: Identifier,
        trial_id: Identifier,
        principal: Annotated[Principal, Depends(actor)],
    ) -> SqlPolicySampleCheck:
        return await service().auto_check_sql_trial_policy(principal, dashboard_id, draft_id, trial_id)

    @routes.get("/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials/{trial_id}/preview")
    async def preview_sql_trial(
        dashboard_id: Identifier,
        draft_id: Identifier,
        trial_id: Identifier,
        principal: Annotated[Principal, Depends(actor)],
    ) -> SqlTrialPreview:
        return await service().preview_sql_trial(principal, dashboard_id, draft_id, trial_id)

    @routes.post("/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/preview")
    async def preview_sql_draft(
        dashboard_id: Identifier,
        draft_id: Identifier,
        payload: SqlDraftPreviewSelection,
        principal: Annotated[Principal, Depends(actor)],
    ) -> SqlDraftPreview:
        return await service().preview_sql_draft(principal, dashboard_id, draft_id, payload.trial_ids)

    router.include_router(routes)
