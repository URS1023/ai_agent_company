"""Versioned sample policies bound to exact SQL/metric/time/mapping subjects.

These internal policies neither authorize source execution nor attest that a formula
answers the user's question. Callers must obtain policy configuration independently
of model output and authorize access to evidence before applying it.
"""

from typing import Protocol

from pydantic import Field, StrictBool

from enterprise_platform.domain.dashboard import DesignSnapshot
from enterprise_platform.domain.data_sources import SourceRef

from .contracts import Contract, Identifier, canonical_hash
from .dashboard_sql_drafts import SqlDraftRecord
from .dashboard_sql_trial_evidence import SqlTrialEvidence
from .dashboard_sql_trial_semantics import SqlSampleCheck, SqlSlotSampleRules, check_sample_rules
from .errors import AccessDenied, Conflict


class SqlSamplePolicy(Contract):
    policy_id: Identifier
    revision: Identifier
    source: SourceRef
    schema_revision: Identifier
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rules: SqlSlotSampleRules
    enabled: StrictBool = False


class SqlPolicySampleCheck(Contract):
    policy_id: Identifier
    policy_revision: Identifier
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: SqlSampleCheck


class SqlSamplePolicies(Protocol):
    def match(self, draft: SqlDraftRecord, slot_id: str) -> tuple[SqlSamplePolicy, ...]:
        """Find enabled exact-subject candidates; multiple matches remain ambiguous."""
        ...

    def get(self, workspace_id: str, policy_id: str, revision: str) -> SqlSamplePolicy:
        """Read the current independently configured policy, with no stale fallback."""
        ...


def apply_sample_policy(
    evidence: SqlTrialEvidence, draft: SqlDraftRecord, design: DesignSnapshot, policy: SqlSamplePolicy
) -> SqlPolicySampleCheck:
    if not policy.enabled:
        raise AccessDenied("sql_sample_policy_disabled")
    proposal = next((slot for slot in draft.proposals.slots if slot.slot_id == policy.rules.slot_id), None)
    if (
        policy.source != draft.source
        or policy.schema_revision != draft.schema_revision
        or proposal is None
        or canonical_hash(proposal.model_dump(mode="json")) != policy.proposal_hash
    ):
        raise Conflict("sql_sample_policy_subject_mismatch")
    result = check_sample_rules(evidence, draft, design, policy.rules)
    return SqlPolicySampleCheck(
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_hash=canonical_hash(policy.model_dump(mode="json")),
        result=result,
    )
