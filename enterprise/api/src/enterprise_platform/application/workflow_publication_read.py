"""Exact secret-free publication evidence; not an enrollment receipt."""

from typing import Literal, Protocol

from pydantic import ConfigDict, Field, field_validator

from .contracts import Contract, Principal
from .errors import DependencyUnavailable
from .workflow_draft_read import canonical_native_uuid
from .workflow_setup_execution import NativeSetupSession


class NativePublicationMetadata(Contract):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    workspace_id: str
    app_id: str
    workflow_id: str
    graph_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_id: Literal["enterprise/enterprise_device_assessment/enterprise_device"]
    tool_name: Literal["evaluate_device"]
    credential_id: str
    node_id: Literal["assessment"]

    @field_validator("workspace_id", "app_id", "workflow_id", "credential_id")
    @classmethod
    def valid_identity(cls, value: str) -> str:
        return canonical_native_uuid(value)


class PublicationReadRejected(DependencyUnavailable):
    code = "native_workflow_publication_read_unavailable"


class WorkflowPublicationReader(Protocol):
    async def read(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        app_id: str,
        workflow_id: str,
        expected_graph_hash: str,
        credential_id: str,
    ) -> NativePublicationMetadata: ...
