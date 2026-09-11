"""Native variable/file-input preflight, without generation or normalized payload persistence.

The caller must authenticate the Account, authorize history/app access and obtain
variables from the effective native configuration. This adapter does not accept
caller-authored variable definitions at an HTTP boundary. Native file resolution may
perform database reads or remote metadata I/O; generation must repeat these checks.
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core.app.app_config.easy_ui_based_app.variables.manager import BasicVariablesConfigManager
from core.app.app_config.workflow_ui_based_app.variables.manager import WorkflowVariablesConfigManager
from core.app.apps.base_app_generator import BaseAppGenerator
from core.app.entities.app_invoke_entities import InvokeFrom, UserFrom
from core.app.file_access import FileAccessScope, bind_file_access_scope
from graphon.variables.input_entities import VariableEntity, VariableEntityType
from models import Account
from models.model import App, AppMode, AppModelConfig, Conversation
from services.workflow_service import WorkflowService


def read_workbench_variables(
    *, app_model: App, conversation: Conversation | None, session: Session
) -> list[VariableEntity]:
    """Select native input variables from current/pinned chat or published workflow.

    Caller supplies an already authorized conversation. No debugger overrides,
    client-authored schemas, model generation or external-data tool execution occur.
    Missing configuration is an error, not an empty set of validation requirements.
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
            return WorkflowVariablesConfigManager.convert(workflow=workflow)
        if app_model.mode not in {AppMode.CHAT, AppMode.AGENT_CHAT}:
            raise ValueError("Not a chat app")
        config_id = conversation.app_model_config_id if conversation is not None else app_model.app_model_config_id
        if config_id is None:
            raise ValueError("Missing model configuration")
        config = session.scalar(
            select(AppModelConfig).where(AppModelConfig.id == config_id, AppModelConfig.app_id == app_model.id)
        )
        if config is None:
            raise ValueError("Missing model configuration")
        variables, _external_data = BasicVariablesConfigManager.convert(config=deepcopy(config.to_dict()))
        return variables
    except (ValueError, TypeError, KeyError):
        raise ValueError("Workbench input configuration unavailable") from None


class _InputPreflight(BaseAppGenerator):
    def check(self, inputs: Mapping[str, JsonValue], variables: Sequence[VariableEntity], tenant_id: str) -> None:
        prepared = self._prepare_user_inputs(user_inputs=deepcopy(inputs), variables=variables, tenant_id=tenant_id)
        for variable in variables:
            original = inputs.get(variable.variable)
            if variable.type == VariableEntityType.FILE_LIST and isinstance(original, list):
                resolved = prepared.get(variable.variable)
                if not isinstance(resolved, list) or len(resolved) != len(original):
                    raise ValueError("Native input preparation dropped selected files")


def require_workbench_inputs(
    *, tenant_id: str, user: Account, inputs: Mapping[str, JsonValue], variables: Sequence[VariableEntity]
) -> None:
    """Reuse native required/default/type/option/file rules under an isolated Account scope."""
    if not isinstance(tenant_id, str) or not tenant_id or not isinstance(user, Account) or not user.id:
        raise ValueError("Workbench inputs unavailable")
    scope = FileAccessScope(
        tenant_id=tenant_id, user_id=user.id, user_from=UserFrom.ACCOUNT, invoke_from=InvokeFrom.EXPLORE
    )
    try:
        with bind_file_access_scope(scope):
            _InputPreflight().check(inputs, variables, tenant_id)
    except (ValueError, TypeError):
        raise ValueError("Workbench inputs unavailable") from None
