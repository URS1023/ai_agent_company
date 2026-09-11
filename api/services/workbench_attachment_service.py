"""Native attachment resolution for installed-chat preflight, not a reusable permit.

The caller authenticates and authorizes the installed app and supplies its native
file configuration. File-valued workflow inputs need their own variable-aware
validation; this helper covers only the explicitly supplied attachment mappings.
With config=None it checks resolution/access only, not whether uploads are enabled;
the future endpoint must check the effective app/conversation/workflow configuration.
Remote mappings may perform native metadata I/O. No generation or database commit
is performed here. Actual generation must repeat native file access checks.
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core.app.app_config.features.file_upload.manager import FileUploadConfigManager
from core.app.entities.app_invoke_entities import InvokeFrom, UserFrom
from core.app.file_access import DatabaseFileAccessController, FileAccessScope, bind_file_access_scope
from factories import file_factory
from graphon.file import FileType, FileUploadConfig
from models import Account
from models.model import App, AppMode, AppModelConfig, Conversation
from services.workflow_service import WorkflowService


def read_workbench_attachment_config(
    *, app_model: App, conversation: Conversation | None, session: Session
) -> FileUploadConfig | None:
    """Read the same effective source as installed generation; None means disabled.

    Caller must authorize the app and conversation before calling. Existing chat
    uses its pinned model config; advanced chat uses the current published workflow.
    Never read debugger overrides or fall back from missing configuration. Conversion
    mutates nested dictionaries, so isolate it from persisted configuration objects.
    """
    try:
        if conversation is not None and conversation.app_id != app_model.id:
            raise ValueError("Foreign conversation")
        if app_model.mode == AppMode.ADVANCED_CHAT:
            workflow = WorkflowService(
                session_maker=sessionmaker(bind=session.get_bind(), expire_on_commit=False)
            ).get_published_workflow(app_model=app_model, session=session)
            if workflow is None:
                raise ValueError("Workflow not published")
            return FileUploadConfigManager.convert(deepcopy(workflow.features_dict), is_vision=False)
        if app_model.mode not in {AppMode.CHAT, AppMode.AGENT_CHAT}:
            raise ValueError("Not an installed chat app")
        config_id = conversation.app_model_config_id if conversation is not None else app_model.app_model_config_id
        if config_id is None:
            raise ValueError("Missing model config")
        config = session.scalar(
            select(AppModelConfig).where(AppModelConfig.id == config_id, AppModelConfig.app_id == app_model.id)
        )
        if config is None:
            raise ValueError("Missing model config")
        return FileUploadConfigManager.convert(deepcopy(config.to_dict()))
    except ValueError:
        raise ValueError("Workbench attachment configuration unavailable") from None


def require_workbench_attachments(
    *,
    tenant_id: str,
    user: Account,
    mappings: Sequence[Mapping[str, JsonValue]],
    config: FileUploadConfig | None,
) -> None:
    """Resolve every selection under a fresh native Account/Explore scope.

    Account semantics remain native tenant-scoped access, not invented end-user
    ownership restrictions. No inherited retrieval grants cross into this scope.
    Unlike the native batch builder, invalid selections are never silently dropped.
    """
    if not isinstance(tenant_id, str) or not tenant_id or not isinstance(user, Account) or not user.id:
        raise ValueError("Workbench attachment unavailable")
    scope = FileAccessScope(
        tenant_id=tenant_id, user_id=user.id, user_from=UserFrom.ACCOUNT, invoke_from=InvokeFrom.EXPLORE
    )
    try:
        with bind_file_access_scope(scope):
            controller = DatabaseFileAccessController()
            image_count = 0
            for mapping in mappings:
                resolved = file_factory.build_from_mapping(
                    mapping=mapping, tenant_id=tenant_id, config=config, access_controller=controller
                )
                if config and config.image_config and resolved.type == FileType.IMAGE:
                    image_count += 1
            if config and config.image_config and image_count > config.image_config.number_limits:
                raise ValueError("Image count exceeds native configuration")
            if config and config.number_limits and len(mappings) > config.number_limits:
                raise ValueError("File count exceeds native configuration")
    except ValueError:
        raise ValueError("Workbench attachment unavailable") from None
