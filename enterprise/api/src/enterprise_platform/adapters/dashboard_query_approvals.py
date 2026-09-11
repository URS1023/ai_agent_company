"""Explicit operator-managed approval file; every lookup reads current bytes.

The caller supplies the path. This adapter never discovers, writes or creates the
file. Operators restrict its filesystem permissions and replace it atomically.
This is a deployable configuration backend, not the future web approval editor.
"""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from enterprise_platform.application.dashboard_query_registry import DashboardQueryApproval
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable
from enterprise_platform.domain.data_sources import Policy


class _ApprovalDocument(Policy):
    schema_version: Literal[1]
    approvals: tuple[DashboardQueryApproval, ...] = Field(max_length=1024)

    @model_validator(mode="after")
    def unique_queries(self) -> Self:
        keys = [(item.workspace_id, item.query_ref, item.query_revision) for item in self.approvals]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate approved query revision")
        return self


class FileDashboardQueryApprovals:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> _ApprovalDocument:
        try:
            with self._path.open("rb") as stream:
                content = stream.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise ValueError("Approval file exceeds limit")
            return _ApprovalDocument.model_validate_json(content, strict=True)
        except (OSError, ValueError):
            raise DependencyUnavailable("dashboard_approval_source_unavailable") from None

    def list_for_actor(self, workspace_id: str, actor_id: str) -> tuple[DashboardQueryApproval, ...]:
        """Discover grants only; current read/device validity is checked by the registry."""
        return tuple(
            sorted(
                (
                    item
                    for item in self._read().approvals
                    if item.workspace_id == workspace_id
                    and item.source.workspace_id == workspace_id
                    and item.enabled
                    and actor_id in item.actor_ids
                ),
                key=lambda item: (item.query_ref, item.query_revision),
            )
        )

    def get(self, workspace_id: str, query_ref: str, query_revision: str) -> DashboardQueryApproval:
        for approval in self._read().approvals:
            if (approval.workspace_id, approval.query_ref, approval.query_revision) == (
                workspace_id,
                query_ref,
                query_revision,
            ):
                return approval
        raise AccessDenied("dashboard_query_not_approved")
