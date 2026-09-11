"""Read-only workbench history checks using native account-scoped services.

Callers must first authenticate the Account, authorize the installed app and
validate request IDs. This helper does not grant app, branch or attachment access
and does not replace the same checks at actual native generation time.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from constants import UUID_NIL
from core.app.task_pipeline.workbench_terminal_metadata import PlainChatTerminalMetadata, read_terminal_metadata
from graphon.enums import WorkflowExecutionStatus
from models import Account
from models.enums import CreatorUserRole, MessageStatus
from models.model import App, AppMode, Conversation
from repositories.api_workflow_run_repository import APIWorkflowRunRepository
from services.conversation_service import ConversationService
from services.errors.message import MessageNotExistsError
from services.message_service import MessageService


def require_workbench_context(
    *,
    app_model: App,
    user: Account,
    session: Session,
    conversation_id: str | None,
    parent_message_id: str | None,
) -> Conversation | None:
    """Require a visible conversation and a parent belonging to that conversation.

    Native empty/root parent markers are accepted without a message lookup.
    Missing, foreign and cross-conversation parents share the native not-found
    error; no message contents are returned and no database writes occur.
    """
    has_parent = bool(parent_message_id and parent_message_id != UUID_NIL)
    if not conversation_id:
        if has_parent:
            raise MessageNotExistsError()
        return None
    conversation = ConversationService.get_conversation(
        app_model=app_model, conversation_id=conversation_id, user=user, session=session
    )
    if has_parent and parent_message_id:
        parent = MessageService.get_message(
            app_model=app_model, user=user, message_id=parent_message_id, session=session
        )
        if parent.conversation_id != conversation.id:
            raise MessageNotExistsError()
    return conversation


@dataclass(frozen=True, slots=True)
class WorkbenchGenerationState:
    message_status: MessageStatus
    workflow_run_id: str | None
    workflow_status: WorkflowExecutionStatus | None
    workflow_finished: bool
    message_terminal: PlainChatTerminalMetadata | None = None


def read_workbench_generation_state(
    *,
    app_model: App,
    user: Account,
    session: Session,
    workflow_runs: APIWorkflowRunRepository,
    conversation_id: str,
    message_id: str,
) -> WorkbenchGenerationState:
    """Read scoped durable state without exposing message content or run payloads.

    Native NORMAL is also the initial message state, so it alone proves nothing.
    Workflow completion requires terminal status and finished_at, and a paused
    message still holds occupancy. This is an observation, not a complete release
    permit: the caller must correlate trusted stream/task identity and message-end
    persistence. Plain chat exposes only validated, server-persisted task metadata;
    missing/old metadata, paused messages and status mismatches remain unproven.
    Inject the configured native repository to preserve SQL/logstore behavior.
    """
    conversation = ConversationService.get_conversation(
        app_model=app_model, conversation_id=conversation_id, user=user, session=session
    )
    message = MessageService.get_message(app_model=app_model, user=user, message_id=message_id, session=session)
    if message.conversation_id != conversation.id:
        raise MessageNotExistsError()
    if message.workflow_run_id is None:
        terminal = (
            read_terminal_metadata(message.message_metadata)
            if app_model.mode in {AppMode.CHAT, AppMode.AGENT_CHAT}
            and message.status in {MessageStatus.NORMAL, MessageStatus.ERROR}
            else None
        )
        if terminal is not None and message.status != (
            MessageStatus.ERROR if terminal.outcome == "failed" else MessageStatus.NORMAL
        ):
            terminal = None
        return WorkbenchGenerationState(message.status, None, None, False, terminal)
    run = workflow_runs.get_workflow_run_by_id(
        tenant_id=app_model.tenant_id, app_id=app_model.id, run_id=message.workflow_run_id
    )
    if run is None or (
        run.id != message.workflow_run_id
        or run.tenant_id != app_model.tenant_id
        or run.app_id != app_model.id
        or run.created_by != user.id
        or run.created_by_role != CreatorUserRole.ACCOUNT
    ):
        raise MessageNotExistsError()
    try:
        status = WorkflowExecutionStatus(run.status)
    except ValueError:
        raise MessageNotExistsError() from None
    finished = (
        message.status != MessageStatus.PAUSED
        and run.finished_at is not None
        and status
        in {
            WorkflowExecutionStatus.SUCCEEDED,
            WorkflowExecutionStatus.PARTIAL_SUCCEEDED,
            WorkflowExecutionStatus.FAILED,
            WorkflowExecutionStatus.STOPPED,
        }
    )
    return WorkbenchGenerationState(message.status, run.id, status, finished)
