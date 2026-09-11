"""Explicitly authorized, version-pinned single-slot trials of persisted SQL drafts.

Trial permission is separate from schema visibility. No binding, dashboard refresh,
approval or model call is performed. This service is not an HTTP endpoint; runtime
must supply a current source-grant authorizer and a bounded read-only executor.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from pydantic import Field

from enterprise_platform.adapters.data_sources import read_fingerprint, validate_sql_structure
from enterprise_platform.adapters.sql_plan_budget import SqlPlanBudget
from enterprise_platform.domain.data_sources import DataSourceError, FrozenRows, ReadLimits, SourceRef, SqlRead

from .contracts import Contract, Identifier, Principal, canonical_hash
from .dashboard_refresh_service import DashboardRecord, DashboardRepository
from .dashboard_sql_drafts import SqlDraftRecord, SqlDraftRepository
from .dashboard_sql_generation import SqlSchemaResolver, validate_proposal_sql
from .dashboard_sql_proposals import Name, parse_sql_proposals
from .errors import AccessDenied, Conflict, DependencyUnavailable, InvalidInput

if TYPE_CHECKING:
    from .dashboard_sql_sample_policy import SqlPolicySampleCheck, SqlSamplePolicies
    from .dashboard_sql_trial_assessment import SqlTrialAssessment
    from .dashboard_sql_trial_evidence import SqlTrialEvidence
    from .dashboard_sql_trial_preview import SqlDraftPreview, SqlTrialPreview
    from .dashboard_sql_trial_result import SqlTrialResult


class SqlTrialCommand(Contract):
    expected_revision: int = Field(strict=True, ge=1)
    expected_design_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    slot_id: Name


class SqlTrialPermit(Contract):
    actor_id: Identifier
    workspace_id: Identifier
    source: SourceRef
    draft_id: Identifier
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    slot_id: Name
    grant_revision: Identifier
    limits: ReadLimits
    plan_budget: SqlPlanBudget


class SqlTrialAuthorizer(Protocol):
    def authorize(self, principal: Principal, draft: SqlDraftRecord, slot_id: str) -> SqlTrialPermit:
        """Resolve an explicit current execution grant; schema visibility is insufficient."""
        ...


class SqlTrialExecutor(Protocol):
    async def execute(self, principal: Principal, permit: SqlTrialPermit, read: SqlRead) -> FrozenRows:
        """Resolve current credentials and enforce permit row/byte/time/plan budgets."""
        ...


@dataclass(frozen=True, slots=True)
class _PreparedTrial:
    read: SqlRead
    permit: SqlTrialPermit
    columns: tuple[str, ...]


class DashboardSqlTrial:
    def __init__(
        self,
        repository: DashboardRepository,
        drafts: SqlDraftRepository,
        schemas: SqlSchemaResolver,
        authorizer: SqlTrialAuthorizer,
        executor: SqlTrialExecutor,
        *,
        sample_policies: "SqlSamplePolicies | None" = None,
    ) -> None:
        self._repository, self._drafts, self._schemas = repository, drafts, schemas
        self._authorizer, self._executor = authorizer, executor
        self._sample_policies = sample_policies

    async def _prepare(
        self, principal: Principal, dashboard_id: str, draft_id: str, command: SqlTrialCommand
    ) -> _PreparedTrial:
        def check_parent(parent: DashboardRecord) -> None:
            if (parent.workspace_id, parent.dashboard_id) != (principal.workspace_id, dashboard_id):
                raise AccessDenied()
            if (parent.revision, parent.design.identity) != (
                command.expected_revision,
                command.expected_design_identity,
            ):
                raise Conflict("dashboard_revision_conflict")

        parent = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        check_parent(parent)
        draft = await asyncio.to_thread(self._drafts.get, principal.workspace_id, draft_id)
        if (draft.workspace_id, draft.dashboard_id, draft.draft_id, draft.source.workspace_id) != (
            principal.workspace_id,
            dashboard_id,
            draft_id,
            principal.workspace_id,
        ):
            raise AccessDenied()
        if (draft.dashboard_revision, draft.design_identity) != (parent.revision, parent.design.identity):
            raise Conflict("sql_trial_stale_draft")
        proposals = parse_sql_proposals(draft.proposals.model_dump_json(), parent.design)
        slot = next((slot for slot in proposals.slots if slot.slot_id == command.slot_id), None)
        if slot is None:
            raise InvalidInput("sql_trial_slot_missing")
        draft_hash = canonical_hash(draft.model_dump(mode="json"))
        permit = await asyncio.to_thread(self._authorizer.authorize, principal, draft, command.slot_id)
        if (
            permit.actor_id != principal.actor_id
            or permit.workspace_id != principal.workspace_id
            or permit.source != draft.source
            or permit.draft_id != draft_id
            or permit.draft_hash != draft_hash
            or permit.slot_id != command.slot_id
        ):
            raise AccessDenied("sql_trial_permit_mismatch")
        schema = await asyncio.to_thread(self._schemas.resolve, principal, draft.source.source_id)
        if (schema.actor_id, schema.workspace_id, schema.source_id) != (
            principal.actor_id,
            principal.workspace_id,
            draft.source.source_id,
        ):
            raise AccessDenied()
        if (schema.source_revision, schema.schema_revision) != (draft.source.revision, draft.schema_revision):
            raise Conflict("sql_trial_stale_schema")
        validate_proposal_sql(proposals, schema)
        tree = validate_sql_structure(
            slot.sql, dialect=schema.dialect, allowed_tables=frozenset(table.name for table in schema.tables)
        )
        current = await asyncio.to_thread(self._repository.get, principal.workspace_id, dashboard_id)
        check_parent(current)
        return _PreparedTrial(
            SqlRead(source=draft.source, read_id=draft_id, revision=draft_hash, sql=slot.sql),
            permit,
            tuple(expression.alias_or_name for expression in tree.selects),
        )

    async def authorize_result(self, principal: Principal, result: "SqlTrialResult") -> None:
        if not principal.can("manage") or not principal.can("run") or result.workspace_id != principal.workspace_id:
            raise AccessDenied()
        command = SqlTrialCommand(
            expected_revision=result.dashboard_revision,
            expected_design_identity=result.design_identity,
            slot_id=result.slot_id,
        )
        prepared = await self._prepare(principal, result.dashboard_id, result.draft_id, command)
        if (
            result.source != prepared.read.source
            or result.draft_hash != prepared.read.revision
            or result.read_fingerprint != read_fingerprint(prepared.read, ())
            or result.columns != prepared.columns
        ):
            raise DependencyUnavailable("sql_trial_capture_mismatch")
        current = await self._prepare(principal, result.dashboard_id, result.draft_id, command)
        if current != prepared:
            raise Conflict("sql_trial_context_changed")

    async def assess_evidence(self, principal: Principal, evidence: "SqlTrialEvidence") -> "SqlTrialAssessment":
        from .dashboard_sql_trial_assessment import assess_sql_trial

        await self.authorize_result(principal, evidence.result)
        draft = await asyncio.to_thread(self._drafts.get, principal.workspace_id, evidence.result.draft_id)
        parent = await asyncio.to_thread(self._repository.get, principal.workspace_id, evidence.result.dashboard_id)
        if (parent.workspace_id, parent.dashboard_id, parent.revision) != (
            principal.workspace_id,
            evidence.result.dashboard_id,
            evidence.result.dashboard_revision,
        ):
            raise Conflict("dashboard_revision_conflict")
        assessment = assess_sql_trial(evidence, draft, parent.design)
        await self.authorize_result(principal, evidence.result)
        return assessment

    async def preview_draft_evidence(
        self, principal: Principal, evidence: tuple["SqlTrialEvidence", ...]
    ) -> "SqlDraftPreview":
        from .dashboard_sql_trial_preview import preview_sql_draft

        if not principal.can("manage") or not principal.can("run"):
            raise AccessDenied()
        if not 1 <= len(evidence) <= 100:
            raise InvalidInput("sql_draft_preview_selection_limit")
        for item in evidence:
            await self.authorize_result(principal, item.result)
        first = evidence[0].result
        draft = await asyncio.to_thread(self._drafts.get, principal.workspace_id, first.draft_id)
        parent = await asyncio.to_thread(self._repository.get, principal.workspace_id, first.dashboard_id)
        if (parent.workspace_id, parent.dashboard_id, parent.revision) != (
            principal.workspace_id,
            first.dashboard_id,
            first.dashboard_revision,
        ):
            raise Conflict("dashboard_revision_conflict")
        preview = preview_sql_draft(evidence, draft, parent.design)
        for item in evidence:
            await self.authorize_result(principal, item.result)
        return preview

    async def preview_evidence(self, principal: Principal, evidence: "SqlTrialEvidence") -> "SqlTrialPreview":
        from .dashboard_sql_trial_preview import preview_sql_trial

        await self.authorize_result(principal, evidence.result)
        draft = await asyncio.to_thread(self._drafts.get, principal.workspace_id, evidence.result.draft_id)
        parent = await asyncio.to_thread(self._repository.get, principal.workspace_id, evidence.result.dashboard_id)
        if (parent.workspace_id, parent.dashboard_id, parent.revision) != (
            principal.workspace_id,
            evidence.result.dashboard_id,
            evidence.result.dashboard_revision,
        ):
            raise Conflict("dashboard_revision_conflict")
        preview = preview_sql_trial(evidence, draft, parent.design)
        await self.authorize_result(principal, evidence.result)
        return preview

    async def auto_check_sample_policy(
        self, principal: Principal, evidence: "SqlTrialEvidence"
    ) -> "SqlPolicySampleCheck":
        await self.authorize_result(principal, evidence.result)
        if self._sample_policies is None:
            raise DependencyUnavailable("sql_sample_policies_unavailable")
        draft = await asyncio.to_thread(self._drafts.get, principal.workspace_id, evidence.result.draft_id)
        if canonical_hash(draft.model_dump(mode="json")) != evidence.result.draft_hash:
            raise Conflict("sql_trial_context_changed")
        matches = await asyncio.to_thread(self._sample_policies.match, draft, evidence.result.slot_id)
        if len(matches) != 1:
            raise Conflict("sql_sample_policy_missing" if not matches else "sql_sample_policy_ambiguous")
        selected = matches[0]
        checked = await self.check_sample_policy(principal, evidence, selected.policy_id, selected.revision)
        if checked.policy_hash != canonical_hash(selected.model_dump(mode="json")):
            raise Conflict("sql_sample_policy_matches_changed")
        current = await asyncio.to_thread(self._sample_policies.match, draft, evidence.result.slot_id)
        if len(current) != 1 or canonical_hash(current[0].model_dump(mode="json")) != checked.policy_hash:
            raise Conflict("sql_sample_policy_matches_changed")
        return checked

    async def check_sample_policy(
        self, principal: Principal, evidence: "SqlTrialEvidence", policy_id: str, revision: str
    ) -> "SqlPolicySampleCheck":
        from .dashboard_sql_sample_policy import apply_sample_policy

        await self.authorize_result(principal, evidence.result)
        if self._sample_policies is None:
            raise DependencyUnavailable("sql_sample_policies_unavailable")
        policy = await asyncio.to_thread(self._sample_policies.get, principal.workspace_id, policy_id, revision)
        if (policy.source.workspace_id, policy.policy_id, policy.revision) != (
            principal.workspace_id,
            policy_id,
            revision,
        ):
            raise Conflict("sql_sample_policy_receipt_mismatch")
        draft = await asyncio.to_thread(self._drafts.get, principal.workspace_id, evidence.result.draft_id)
        parent = await asyncio.to_thread(self._repository.get, principal.workspace_id, evidence.result.dashboard_id)
        if (parent.workspace_id, parent.dashboard_id, parent.revision) != (
            principal.workspace_id,
            evidence.result.dashboard_id,
            evidence.result.dashboard_revision,
        ):
            raise Conflict("dashboard_revision_conflict")
        checked = apply_sample_policy(evidence, draft, parent.design, policy)
        await self.authorize_result(principal, evidence.result)
        current = await asyncio.to_thread(self._sample_policies.get, principal.workspace_id, policy_id, revision)
        if canonical_hash(current.model_dump(mode="json")) != checked.policy_hash:
            raise Conflict("sql_sample_policy_changed")
        return checked

    async def run(self, principal: Principal, dashboard_id: str, draft_id: str, command: SqlTrialCommand) -> FrozenRows:
        if not principal.can("manage") or not principal.can("run"):
            raise AccessDenied()
        prepared = await self._prepare(principal, dashboard_id, draft_id, command)
        started_at = datetime.now(UTC)
        try:
            async with asyncio.timeout(prepared.permit.limits.timeout_seconds):
                capture = await self._executor.execute(principal, prepared.permit, prepared.read)
        except (DataSourceError, TimeoutError):
            raise DependencyUnavailable("sql_trial_read_failed") from None
        if (
            capture.source != prepared.read.source
            or capture.read_id != prepared.read.read_id
            or capture.read_revision != prepared.read.revision
            or capture.read_fingerprint != read_fingerprint(prepared.read, ())
            or capture.columns != prepared.columns
            or not started_at <= capture.captured_at <= datetime.now(UTC)
        ):
            raise DependencyUnavailable("sql_trial_capture_mismatch")
        limits = prepared.permit.limits
        if (
            len(capture.rows) > limits.max_rows
            or sum(len(str(value).encode("utf-8")) for row in capture.rows for value in row) > limits.max_bytes
        ):
            raise DependencyUnavailable("sql_trial_capture_limit")
        current = await self._prepare(principal, dashboard_id, draft_id, command)
        if current != prepared:
            raise Conflict("sql_trial_context_changed")
        return capture
