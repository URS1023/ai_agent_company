"""Dashboard read/refresh API boundary; persisted views follow workspace read policy."""

import asyncio
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from enterprise_platform.domain.dashboard import DashboardError, DesignSnapshot, RefreshState

from .contracts import Principal, canonical_hash
from .dashboard_binding_service import DashboardBindingService
from .dashboard_contracts import (
    DashboardBindingView,
    DashboardBindingWrite,
    DashboardCreateCommand,
    DashboardPage,
    DashboardQueryCatalog,
    DashboardSummary,
    DashboardTemplateCatalog,
    DashboardTemplateView,
    DashboardView,
    as_dashboard_bindings,
    as_dashboard_view,
)
from .dashboard_query_registry import DashboardQueryDiscovery
from .dashboard_refresh_service import DashboardRecord, DashboardRefreshService, DashboardRepository
from .dashboard_sql_generation import (
    DashboardSqlGeneration,
    GenerateSqlCommand,
    SqlDraftInspection,
    SqlGenerationReceipt,
)
from .dashboard_sql_sample_policy import SqlPolicySampleCheck
from .dashboard_sql_trial import DashboardSqlTrial, SqlTrialCommand
from .dashboard_sql_trial_assessment import SqlTrialAssessment
from .dashboard_sql_trial_evidence import SqlTrialEvidence, SqlTrialEvidenceRepository, SqlTrialPage
from .dashboard_sql_trial_preview import SqlDraftPreview, SqlTrialPreview
from .dashboard_sql_trial_result import SqlTrialResult, as_sql_trial_result
from .errors import AccessDenied, Conflict, DependencyUnavailable, InvalidInput, NotFound, PersistenceError


class DashboardTemplates(Protocol):
    def list(self) -> tuple[DesignSnapshot, ...]: ...

    def get(self, template_id: str) -> DesignSnapshot: ...


class DashboardService:
    def __init__(
        self,
        repository: DashboardRepository,
        refresh: DashboardRefreshService,
        *,
        templates: DashboardTemplates | None = None,
        binding_service: DashboardBindingService | None = None,
        query_discovery: DashboardQueryDiscovery | None = None,
        sql_generation: DashboardSqlGeneration | None = None,
        sql_trial: DashboardSqlTrial | None = None,
        sql_trial_evidence: SqlTrialEvidenceRepository | None = None,
    ) -> None:
        self._repository, self._refresh = repository, refresh
        self._templates = templates
        self._binding_service = binding_service
        self._query_discovery = query_discovery
        self._sql_generation = sql_generation
        self._sql_trial = sql_trial
        self._sql_trial_evidence = sql_trial_evidence

    async def trial_sql(
        self, principal: Principal, dashboard_id: str, draft_id: str, command: SqlTrialCommand
    ) -> SqlTrialResult:
        if not principal.can("manage") or not principal.can("run"):
            raise AccessDenied()
        if self._sql_trial is None:
            raise DependencyUnavailable("dashboard_sql_trial_unavailable")
        capture = await self._sql_trial.run(principal, dashboard_id, draft_id, command)
        result = as_sql_trial_result(principal, dashboard_id, draft_id, command, capture)
        if self._sql_trial_evidence is not None:
            evidence = SqlTrialEvidence(
                trial_id=str(uuid4()), actor_id=principal.actor_id, recorded_at=datetime.now(UTC), result=result
            )
            expected = canonical_hash(evidence.model_dump(mode="json"))
            saved = await asyncio.to_thread(self._sql_trial_evidence.create, evidence)
            if canonical_hash(saved.model_dump(mode="json")) != expected:
                raise PersistenceError("sql_trial_evidence_receipt_mismatch")
        return result

    async def _read_sql_trial(
        self, principal: Principal, dashboard_id: str, draft_id: str, trial_id: str
    ) -> SqlTrialEvidence:
        if not principal.can("manage") or not principal.can("run"):
            raise AccessDenied()
        if self._sql_trial is None or self._sql_trial_evidence is None:
            raise DependencyUnavailable("dashboard_sql_trial_unavailable")
        evidence = await asyncio.to_thread(self._sql_trial_evidence.get, principal.workspace_id, trial_id)
        result = evidence.result
        if (evidence.trial_id, result.workspace_id, result.dashboard_id, result.draft_id) != (
            trial_id,
            principal.workspace_id,
            dashboard_id,
            draft_id,
        ):
            raise AccessDenied()
        return evidence

    async def inspect_sql_trial(
        self, principal: Principal, dashboard_id: str, draft_id: str, trial_id: str
    ) -> SqlTrialEvidence:
        evidence = await self._read_sql_trial(principal, dashboard_id, draft_id, trial_id)
        if self._sql_trial is None:
            raise DependencyUnavailable("dashboard_sql_trial_unavailable")
        await self._sql_trial.authorize_result(principal, evidence.result)
        return evidence

    async def assess_sql_trial(
        self, principal: Principal, dashboard_id: str, draft_id: str, trial_id: str
    ) -> SqlTrialAssessment:
        evidence = await self._read_sql_trial(principal, dashboard_id, draft_id, trial_id)
        if self._sql_trial is None:
            raise DependencyUnavailable("dashboard_sql_trial_unavailable")
        return await self._sql_trial.assess_evidence(principal, evidence)

    async def preview_sql_draft(
        self, principal: Principal, dashboard_id: str, draft_id: str, trial_ids: tuple[str, ...]
    ) -> SqlDraftPreview:
        if not principal.can("manage") or not principal.can("run"):
            raise AccessDenied()
        if not 1 <= len(trial_ids) <= 100 or len(set(trial_ids)) != len(trial_ids):
            raise InvalidInput("sql_draft_preview_selection_invalid")
        if self._sql_trial is None:
            raise DependencyUnavailable("dashboard_sql_trial_unavailable")
        evidence: list[SqlTrialEvidence] = []
        total_bytes = 0
        for trial_id in trial_ids:
            item = await self._read_sql_trial(principal, dashboard_id, draft_id, trial_id)
            total_bytes += len(item.model_dump_json().encode("utf-8"))
            if total_bytes > 2 * 1024 * 1024:
                raise InvalidInput("sql_draft_preview_size_limit")
            evidence.append(item)
        return await self._sql_trial.preview_draft_evidence(principal, tuple(evidence))

    async def preview_sql_trial(
        self, principal: Principal, dashboard_id: str, draft_id: str, trial_id: str
    ) -> SqlTrialPreview:
        evidence = await self._read_sql_trial(principal, dashboard_id, draft_id, trial_id)
        if self._sql_trial is None:
            raise DependencyUnavailable("dashboard_sql_trial_unavailable")
        return await self._sql_trial.preview_evidence(principal, evidence)

    async def auto_check_sql_trial_policy(
        self, principal: Principal, dashboard_id: str, draft_id: str, trial_id: str
    ) -> SqlPolicySampleCheck:
        evidence = await self._read_sql_trial(principal, dashboard_id, draft_id, trial_id)
        if self._sql_trial is None:
            raise DependencyUnavailable("dashboard_sql_trial_unavailable")
        return await self._sql_trial.auto_check_sample_policy(principal, evidence)

    async def check_sql_trial_policy(
        self, principal: Principal, dashboard_id: str, draft_id: str, trial_id: str, policy_id: str, revision: str
    ) -> SqlPolicySampleCheck:
        evidence = await self._read_sql_trial(principal, dashboard_id, draft_id, trial_id)
        if self._sql_trial is None:
            raise DependencyUnavailable("dashboard_sql_trial_unavailable")
        return await self._sql_trial.check_sample_policy(principal, evidence, policy_id, revision)

    async def list_sql_trials(
        self, principal: Principal, dashboard_id: str, draft_id: str, *, after: str | None = None, limit: int = 20
    ) -> SqlTrialPage:
        if not principal.can("manage") or not principal.can("run"):
            raise AccessDenied()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("sql_trial_page_limit")
        if self._sql_trial_evidence is None:
            raise DependencyUnavailable("dashboard_sql_trial_unavailable")
        parent = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        if (parent.workspace_id, parent.dashboard_id) != (principal.workspace_id, dashboard_id):
            raise AccessDenied()
        items = await asyncio.to_thread(
            self._sql_trial_evidence.list, principal.workspace_id, dashboard_id, draft_id, after=after, limit=limit + 1
        )
        if any(
            (item.workspace_id, item.dashboard_id, item.draft_id) != (principal.workspace_id, dashboard_id, draft_id)
            for item in items
        ):
            raise AccessDenied()
        identifiers = [item.trial_id for item in items]
        if len(items) > limit + 1 or len(set(identifiers)) != len(identifiers) or after in identifiers:
            raise PersistenceError("sql_trial_page_invalid")
        return SqlTrialPage(items=items[:limit], next_cursor=items[limit - 1].trial_id if len(items) > limit else None)

    async def generate_sql(
        self, principal: Principal, dashboard_id: str, command: GenerateSqlCommand
    ) -> SqlGenerationReceipt:
        if not principal.can("manage"):
            raise AccessDenied()
        if self._sql_generation is None:
            raise DependencyUnavailable("dashboard_sql_generation_unavailable")
        return await self._sql_generation.generate(principal, dashboard_id, command)

    async def inspect_sql_draft(self, principal: Principal, dashboard_id: str, draft_id: str) -> SqlDraftInspection:
        if not principal.can("manage"):
            raise AccessDenied()
        if self._sql_generation is None:
            raise DependencyUnavailable("dashboard_sql_generation_unavailable")
        return await self._sql_generation.inspect_draft(principal, dashboard_id, draft_id)

    async def queries(self, principal: Principal) -> DashboardQueryCatalog:
        if not principal.can("manage"):
            raise AccessDenied()
        if self._query_discovery is None:
            raise DependencyUnavailable("dashboard_queries_unavailable")
        return DashboardQueryCatalog(items=await asyncio.to_thread(self._query_discovery.discover, principal))

    async def get_bindings(self, principal: Principal, dashboard_id: str) -> DashboardBindingView:
        if not principal.can("manage"):
            raise AccessDenied()
        record = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        if (record.workspace_id, record.dashboard_id) != (principal.workspace_id, dashboard_id):
            raise AccessDenied()
        return as_dashboard_bindings(record)

    async def save_bindings(
        self, principal: Principal, dashboard_id: str, command: DashboardBindingWrite
    ) -> DashboardBindingView:
        if not principal.can("manage"):
            raise AccessDenied()
        if self._binding_service is None:
            raise DependencyUnavailable("dashboard_bindings_unavailable")
        try:
            bindings = tuple(item.execution() for item in command.bindings)
        except DashboardError as error:
            raise InvalidInput(error.code) from None
        saved = await self._binding_service.save(
            principal,
            dashboard_id,
            expected_revision=command.expected_revision,
            expected_design_identity=command.expected_design_identity,
            bindings=bindings,
        )
        if (saved.workspace_id, saved.dashboard_id) != (principal.workspace_id, dashboard_id):
            raise AccessDenied()
        return as_dashboard_bindings(saved)

    async def create(self, principal: Principal, command: DashboardCreateCommand, *, request_key: str) -> DashboardView:
        """Creation retries return the current record, verified by an immutable request fingerprint.

        Workspace/actor/key determine the ID, and the unique primary key arbitrates
        races. Never overwrite a winner or reset its refreshed data on retry.
        """
        if not principal.can("manage"):
            raise AccessDenied()
        if not request_key.strip() or len(request_key) > 128:
            raise InvalidInput("invalid_request_key")
        dashboard_id = canonical_hash(["dashboard-create-v1", principal.workspace_id, principal.actor_id, request_key])
        request_hash = canonical_hash(command.model_dump())

        def receipt(record: DashboardRecord) -> DashboardView:
            if (record.workspace_id, record.dashboard_id) != (principal.workspace_id, dashboard_id):
                raise AccessDenied()
            if (record.creation_request_hash and record.creation_request_hash != request_hash) or (
                not record.creation_request_hash and command.name
            ):
                raise Conflict("dashboard_creation_conflict")
            if (record.design.template_id, record.design.identity) != (
                command.template_id,
                command.expected_design_identity,
            ):
                raise Conflict("dashboard_creation_conflict")
            return as_dashboard_view(record)

        try:
            existing = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        except NotFound:
            pass
        else:
            return receipt(existing)
        if self._templates is None:
            raise DependencyUnavailable("dashboard_template_catalog_unavailable")
        design = await asyncio.to_thread(self._templates.get, command.template_id)
        if (design.template_id, design.identity) != (command.template_id, command.expected_design_identity):
            raise Conflict("dashboard_template_changed")
        record = DashboardRecord(
            principal.workspace_id,
            dashboard_id,
            1,
            design,
            (),
            RefreshState(design.identity),
            name=command.name or design.template_id,
            creation_request_hash=request_hash,
        )
        try:
            saved = await asyncio.to_thread(self._repository.create, record, actor_id=principal.actor_id)
        except Conflict as conflict:
            try:
                saved = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
            except NotFound:
                raise conflict from None
        return receipt(saved)

    async def templates(self, principal: Principal) -> DashboardTemplateCatalog:
        if not principal.can("read"):
            raise AccessDenied()
        if self._templates is None:
            raise DependencyUnavailable("dashboard_template_catalog_unavailable")
        templates = await asyncio.to_thread(self._templates.list)
        return DashboardTemplateCatalog(
            items=tuple(
                DashboardTemplateView(
                    template_id=item.template_id,
                    template_revision=item.template_revision,
                    design_identity=item.identity,
                    renderer_build_id=item.renderer_build_id,
                    slots=item.slots,
                )
                for item in templates
            )
        )

    async def list(self, principal: Principal, *, after: str | None = None, limit: int = 50) -> DashboardPage:
        if not principal.can("read"):
            raise AccessDenied()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("invalid_dashboard_page")
        records = await asyncio.to_thread(self._repository.list, principal.workspace_id, after=after, limit=limit + 1)
        if any(record.workspace_id != principal.workspace_id for record in records):
            raise AccessDenied()
        page = records[:limit]
        return DashboardPage(
            items=tuple(
                DashboardSummary(
                    id=record.dashboard_id,
                    name=record.name or record.dashboard_id,
                    revision=record.revision,
                    template_id=record.design.template_id,
                    status=record.state.status,
                )
                for record in page
            ),
            next_cursor=page[-1].dashboard_id if len(records) > limit else None,
        )

    async def get(self, principal: Principal, dashboard_id: str) -> DashboardView:
        if not principal.can("read"):
            raise AccessDenied()
        record = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        if (record.workspace_id, record.dashboard_id) != (principal.workspace_id, dashboard_id):
            raise AccessDenied()
        try:
            return as_dashboard_view(record)
        except DashboardError:
            raise PersistenceError("dashboard_document_invalid") from None

    async def refresh(self, principal: Principal, dashboard_id: str, *, expected_revision: int) -> DashboardView:
        if not principal.can("run"):
            raise AccessDenied()
        await self._refresh.refresh(principal, dashboard_id, expected_revision=expected_revision)
        return await self.get(principal, dashboard_id)
