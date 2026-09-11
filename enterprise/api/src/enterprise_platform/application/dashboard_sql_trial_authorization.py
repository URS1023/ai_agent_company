"""Explicit source/actor/dashboard trial grants, independent of schema visibility.

The full current grant hash pins the permit, so allowlist or budget changes do not
depend on an operator remembering to increment a revision. No grant is created here.
Current source/schema and template validation remain the trial service's responsibility.
"""

from typing import Protocol, Self

from pydantic import Field, StrictBool, model_validator

from enterprise_platform.adapters.sql_plan_budget import SqlPlanBudget
from enterprise_platform.domain.data_sources import ReadLimits, SourceRef

from .contracts import Contract, Identifier, Principal, canonical_hash
from .dashboard_sql_drafts import SqlDraftRecord
from .dashboard_sql_trial import SqlTrialPermit
from .errors import AccessDenied, Conflict, InvalidInput


class SqlTrialGrant(Contract):
    source: SourceRef
    actor_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=1000)
    dashboard_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=1000)
    limits: ReadLimits
    plan_budget: SqlPlanBudget
    enabled: StrictBool = False

    @model_validator(mode="after")
    def unique_allowlists(self) -> Self:
        if len(set(self.actor_ids)) != len(self.actor_ids) or len(set(self.dashboard_ids)) != len(self.dashboard_ids):
            raise ValueError("Duplicate trial authority")
        return self


class SqlTrialGrants(Protocol):
    def get(self, workspace_id: str, source_id: str) -> SqlTrialGrant: ...


class RegisteredSqlTrialAuthorizer:
    def __init__(self, grants: SqlTrialGrants) -> None:
        self._grants = grants

    def authorize(self, principal: Principal, draft: SqlDraftRecord, slot_id: str) -> SqlTrialPermit:
        if (
            not principal.can("manage")
            or not principal.can("run")
            or draft.workspace_id != principal.workspace_id
            or draft.source.workspace_id != principal.workspace_id
        ):
            raise AccessDenied()
        if slot_id not in {slot.slot_id for slot in draft.proposals.slots}:
            raise InvalidInput("sql_trial_slot_missing")
        grant = self._grants.get(principal.workspace_id, draft.source.source_id)
        if (
            not grant.enabled
            or principal.actor_id not in grant.actor_ids
            or draft.dashboard_id not in grant.dashboard_ids
            or (grant.source.workspace_id, grant.source.source_id) != (principal.workspace_id, draft.source.source_id)
        ):
            raise AccessDenied("sql_trial_not_granted")
        if grant.source.revision != draft.source.revision:
            raise Conflict("sql_trial_source_changed")
        return SqlTrialPermit(
            actor_id=principal.actor_id,
            workspace_id=principal.workspace_id,
            source=draft.source,
            draft_id=draft.draft_id,
            draft_hash=canonical_hash(draft.model_dump(mode="json")),
            slot_id=slot_id,
            grant_revision=canonical_hash(grant.model_dump(mode="json")),
            limits=grant.limits,
            plan_budget=grant.plan_budget,
        )
