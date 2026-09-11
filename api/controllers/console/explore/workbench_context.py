"""Native workbench preflight, observations and identity-bound chat delegation.

InstalledAppResource retains login, account initialization, tenant installation and
enterprise app access checks. Expected identity headers bind the same Account used
for history reads. Preflights may fetch remote file metadata but never generate
replies. The chat route delegates to the original native handler after identity
validation. Enterprise branch/ledger authority remains outside these endpoints.
"""

from typing import Literal

from flask import request
from pydantic import BaseModel, ConfigDict, JsonValue, field_validator
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from controllers.common.schema import register_response_schema_models, register_schema_models
from controllers.console import console_ns
from controllers.console.app.error import AppUnavailableError
from controllers.console.app.wraps import with_session
from controllers.console.explore.error import NotChatAppError
from controllers.console.explore.wraps import InstalledAppResource
from controllers.console.wraps import with_current_user
from core.app.task_pipeline.workbench_terminal_metadata import PlainChatTerminalMetadata
from graphon.enums import WorkflowExecutionStatus
from libs import helper
from models import Account
from models.enums import MessageStatus
from models.model import App, AppMode, InstalledApp
from repositories.factory import DifyAPIRepositoryFactory
from services.errors.conversation import ConversationNotExistsError
from services.errors.message import MessageNotExistsError
from services.workbench_attachment_service import read_workbench_attachment_config, require_workbench_attachments
from services.workbench_context_service import read_workbench_generation_state, require_workbench_context
from services.workbench_input_service import read_workbench_variables, require_workbench_inputs


class WorkbenchContextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    conversation_id: str | None = None
    parent_message_id: str | None = None

    @field_validator("conversation_id", "parent_message_id")
    @classmethod
    def validate_context_id(cls, value: str | None) -> str | None:
        return helper.uuid_value(value) if value else None


class WorkbenchContextResponse(BaseModel):
    workspace_id: str
    actor_id: str
    installed_app_id: str
    context_verified: Literal[True] = True
    attachments_verified: Literal[False] = False
    branch_verified: Literal[False] = False


class WorkbenchAttachmentPayload(WorkbenchContextPayload):
    files: list[dict[str, JsonValue]]


class WorkbenchAttachmentResponse(BaseModel):
    workspace_id: str
    actor_id: str
    installed_app_id: str
    file_count: int
    selected_files_verified: Literal[True] = True
    input_files_verified: Literal[False] = False
    branch_verified: Literal[False] = False


class WorkbenchInputPayload(WorkbenchContextPayload):
    inputs: dict[str, JsonValue]


class WorkbenchChatPayload(WorkbenchInputPayload):
    query: str
    files: list[dict[str, JsonValue]] | None = None
    retriever_from: str = "explore_app"


class WorkbenchInputResponse(BaseModel):
    workspace_id: str
    actor_id: str
    installed_app_id: str
    inputs_verified: Literal[True] = True
    selected_files_verified: Literal[False] = False
    branch_verified: Literal[False] = False


class WorkbenchGenerationStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    conversation_id: str
    message_id: str

    @field_validator("conversation_id", "message_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return helper.uuid_value(value)


class WorkbenchGenerationStateResponse(BaseModel):
    workspace_id: str
    actor_id: str
    installed_app_id: str
    conversation_id: str
    message_id: str
    message_status: MessageStatus
    workflow_run_id: str | None
    workflow_status: WorkflowExecutionStatus | None
    workflow_finished: bool
    message_terminal: PlainChatTerminalMetadata | None = None


register_schema_models(
    console_ns,
    WorkbenchContextPayload,
    WorkbenchGenerationStatePayload,
    WorkbenchAttachmentPayload,
    WorkbenchInputPayload,
    WorkbenchChatPayload,
)
register_response_schema_models(
    console_ns,
    WorkbenchContextResponse,
    WorkbenchGenerationStateResponse,
    WorkbenchAttachmentResponse,
    WorkbenchInputResponse,
)


def _workbench_app(current_user: Account, installed_app: InstalledApp) -> tuple[str, App]:
    workspace_id = current_user.current_tenant_id
    if (
        not workspace_id
        or request.headers.get("X-Enterprise-Expected-Workspace") != workspace_id
        or request.headers.get("X-Enterprise-Expected-Actor") != current_user.id
        or installed_app.tenant_id != workspace_id
    ):
        raise Conflict("Current identity does not match expected context")
    app_model = installed_app.app
    if app_model is None:
        raise AppUnavailableError()
    if app_model.mode not in {AppMode.CHAT, AppMode.AGENT_CHAT, AppMode.ADVANCED_CHAT}:
        raise NotChatAppError()
    return workspace_id, app_model


@console_ns.route("/installed-apps/<uuid:installed_app_id>/enterprise/workbench/context-check")
class WorkbenchContextApi(InstalledAppResource):
    @console_ns.expect(console_ns.models[WorkbenchContextPayload.__name__])
    @console_ns.response(200, "Native context checked", console_ns.models[WorkbenchContextResponse.__name__])
    @with_current_user
    @with_session
    def post(self, session: Session, current_user: Account, installed_app: InstalledApp):
        """Check history without generation, last-used updates or database commits."""
        workspace_id, app_model = _workbench_app(current_user, installed_app)
        payload = WorkbenchContextPayload.model_validate(console_ns.payload or {})
        try:
            require_workbench_context(
                app_model=app_model,
                user=current_user,
                session=session,
                conversation_id=payload.conversation_id,
                parent_message_id=payload.parent_message_id,
            )
        except (ConversationNotExistsError, MessageNotExistsError):
            raise NotFound("Chat context not found") from None
        result = WorkbenchContextResponse(
            workspace_id=workspace_id, actor_id=current_user.id, installed_app_id=installed_app.id
        )
        return result.model_dump(mode="json"), 200, {"Cache-Control": "private, no-store"}


@console_ns.route("/installed-apps/<uuid:installed_app_id>/enterprise/workbench/attachment-check")
class WorkbenchAttachmentApi(InstalledAppResource):
    @console_ns.expect(console_ns.models[WorkbenchAttachmentPayload.__name__])
    @console_ns.response(200, "Selected attachments checked", console_ns.models[WorkbenchAttachmentResponse.__name__])
    @with_current_user
    @with_session
    def post(self, session: Session, current_user: Account, installed_app: InstalledApp):
        """Check selected files under native context/config, not file-valued inputs."""
        workspace_id, app_model = _workbench_app(current_user, installed_app)
        payload = WorkbenchAttachmentPayload.model_validate(console_ns.payload or {})
        try:
            conversation = require_workbench_context(
                app_model=app_model,
                user=current_user,
                session=session,
                conversation_id=payload.conversation_id,
                parent_message_id=payload.parent_message_id,
            )
        except (ConversationNotExistsError, MessageNotExistsError):
            raise NotFound("Chat context not found") from None
        try:
            config = read_workbench_attachment_config(app_model=app_model, conversation=conversation, session=session)
            if payload.files and config is None:
                raise ValueError("Uploads disabled")
            require_workbench_attachments(
                tenant_id=app_model.tenant_id, user=current_user, mappings=payload.files, config=config
            )
        except ValueError:
            raise BadRequest("Selected attachments unavailable") from None
        result = WorkbenchAttachmentResponse(
            workspace_id=workspace_id,
            actor_id=current_user.id,
            installed_app_id=installed_app.id,
            file_count=len(payload.files),
        )
        return result.model_dump(mode="json"), 200, {"Cache-Control": "private, no-store"}


@console_ns.route("/installed-apps/<uuid:installed_app_id>/enterprise/workbench/input-check")
class WorkbenchInputApi(InstalledAppResource):
    @console_ns.expect(console_ns.models[WorkbenchInputPayload.__name__])
    @console_ns.response(200, "Native inputs checked", console_ns.models[WorkbenchInputResponse.__name__])
    @with_current_user
    @with_session
    def post(self, session: Session, current_user: Account, installed_app: InstalledApp):
        """Check native form inputs, including files; never return their resolved values."""
        workspace_id, app_model = _workbench_app(current_user, installed_app)
        payload = WorkbenchInputPayload.model_validate(console_ns.payload or {})
        try:
            conversation = require_workbench_context(
                app_model=app_model,
                user=current_user,
                session=session,
                conversation_id=payload.conversation_id,
                parent_message_id=payload.parent_message_id,
            )
        except (ConversationNotExistsError, MessageNotExistsError):
            raise NotFound("Chat context not found") from None
        try:
            variables = read_workbench_variables(app_model=app_model, conversation=conversation, session=session)
            require_workbench_inputs(
                tenant_id=app_model.tenant_id, user=current_user, inputs=payload.inputs, variables=variables
            )
        except ValueError:
            raise BadRequest("Inputs unavailable") from None
        result = WorkbenchInputResponse(
            workspace_id=workspace_id, actor_id=current_user.id, installed_app_id=installed_app.id
        )
        return result.model_dump(mode="json"), 200, {"Cache-Control": "private, no-store"}


@console_ns.route("/installed-apps/<uuid:installed_app_id>/enterprise/workbench/chat-messages")
class WorkbenchChatApi(InstalledAppResource):
    @console_ns.expect(console_ns.models[WorkbenchChatPayload.__name__])
    @console_ns.response(200, "Native chat stream")
    @with_current_user
    def post(self, current_user: Account, installed_app: InstalledApp):
        """Validate identity before native writes; preserve original payload and response.

        Runtime import avoids completion's route-registration cycle. Delegating
        through its decorated handler retains native Account/session injection,
        validation, last-used updates, generation and error handling. Enterprise
        branch/message claiming remains the gateway's separate responsibility.
        """
        from controllers.console.explore.completion import ChatApi

        _workbench_app(current_user, installed_app)
        WorkbenchChatPayload.model_validate(console_ns.payload or {})
        return ChatApi().post(installed_app=installed_app)


@console_ns.route("/installed-apps/<uuid:installed_app_id>/enterprise/workbench/generation-state")
class WorkbenchGenerationStateApi(InstalledAppResource):
    @console_ns.expect(console_ns.models[WorkbenchGenerationStatePayload.__name__])
    @console_ns.response(200, "Native state observation", console_ns.models[WorkbenchGenerationStateResponse.__name__])
    @with_current_user
    @with_session
    def post(self, session: Session, current_user: Account, installed_app: InstalledApp):
        """Observe persisted metadata, not a reusable completion or send permit."""
        workspace_id, app_model = _workbench_app(current_user, installed_app)
        payload = WorkbenchGenerationStatePayload.model_validate(console_ns.payload or {})
        runs = DifyAPIRepositoryFactory.create_api_workflow_run_repository(
            sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        )
        try:
            observation = read_workbench_generation_state(
                app_model=app_model,
                user=current_user,
                session=session,
                workflow_runs=runs,
                conversation_id=payload.conversation_id,
                message_id=payload.message_id,
            )
        except (ConversationNotExistsError, MessageNotExistsError):
            raise NotFound("Chat context not found") from None
        result = WorkbenchGenerationStateResponse(
            workspace_id=workspace_id,
            actor_id=current_user.id,
            installed_app_id=installed_app.id,
            conversation_id=payload.conversation_id,
            message_id=payload.message_id,
            message_status=observation.message_status,
            workflow_run_id=observation.workflow_run_id,
            workflow_status=observation.workflow_status,
            workflow_finished=observation.workflow_finished,
            message_terminal=observation.message_terminal,
        )
        return result.model_dump(mode="json"), 200, {"Cache-Control": "private, no-store"}
