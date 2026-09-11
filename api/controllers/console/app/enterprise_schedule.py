"""Authenticated read-only ownership snapshot; not a scheduler lease endpoint.

Uses the same native setup/login/member/edit/app-RBAC stack as published workflow
reads. No graph, credential, schedule details or other workspace data is returned.
The enterprise feature flag remains required; ordinary native routes are unchanged.
"""

from typing import Literal
from uuid import UUID

from flask import request
from flask_restx import Resource
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from werkzeug.exceptions import BadRequest, Conflict, Forbidden

from configs import dify_config
from controllers.console import console_ns
from controllers.console.app.wraps import get_app_model
from controllers.console.wraps import (
    RBACPermission,
    RBACResourceScope,
    account_initialization_required,
    edit_permission_required,
    rbac_permission_required,
    setup_required,
)
from extensions.ext_database import db
from libs.login import login_required
from models import App
from models.model import AppMode
from services.enterprise_schedule_inspection import ScheduleInspectionError, inspect_schedule_owner


class _Headers(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    workspace_id: str
    workflow_id: str
    graph_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["inspect-native-owner"]

    @field_validator("workspace_id", "workflow_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        parsed = UUID(value)
        if not parsed.int or str(parsed) != value:
            raise ValueError("Canonical nonzero UUID required")
        return value


@console_ns.route("/apps/<uuid:app_id>/enterprise/schedule-owner")
class EnterpriseScheduleOwnerApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    @edit_permission_required
    @rbac_permission_required(RBACResourceScope.APP, RBACPermission.APP_VIEW_LAYOUT)
    @get_app_model(mode=[AppMode.ADVANCED_CHAT, AppMode.WORKFLOW])
    def get(self, app_model: App) -> tuple[dict[str, object], int, dict[str, str]]:
        """Inspect one pinned publication and any app-level native schedule plan."""
        if not dify_config.ENTERPRISE_WORKFLOW_SETUP_ENABLED:
            raise Forbidden("Enterprise workflow setup is disabled")
        try:
            headers = _Headers.model_validate(
                {
                    "workspace_id": request.headers.get("X-Enterprise-Expected-Workspace"),
                    "workflow_id": request.headers.get("X-Enterprise-Expected-Workflow"),
                    "graph_hash": request.headers.get("X-Enterprise-Expected-Graph-Hash"),
                    "operation": request.headers.get("X-Enterprise-Schedule-Operation"),
                }
            )
        except ValidationError:
            raise BadRequest("Invalid schedule inspection headers") from None
        if app_model.tenant_id != headers.workspace_id:
            raise Conflict("Expected workspace does not match the app")
        try:
            result = inspect_schedule_owner(
                db.session(),
                workspace_id=headers.workspace_id,
                app_id=app_model.id,
                workflow_id=headers.workflow_id,
                expected_hash=headers.graph_hash,
            )
        except ScheduleInspectionError:
            raise Conflict("Native schedule ownership could not be verified") from None
        if (result.workspace_id, result.app_id, result.workflow_id, result.graph_hash) != (
            headers.workspace_id,
            app_model.id,
            headers.workflow_id,
            headers.graph_hash,
        ) or type(result.native_owner_present) is not bool:
            raise Conflict("Native schedule inspection receipt mismatch")
        return (
            {
                "workspace_id": result.workspace_id,
                "app_id": result.app_id,
                "workflow_id": result.workflow_id,
                "graph_hash": result.graph_hash,
                "native_owner_present": result.native_owner_present,
            },
            200,
            {"X-Enterprise-Workspace": headers.workspace_id, "Cache-Control": "private, no-store"},
        )
