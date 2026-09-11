"""Lazy workbench wiring against the separate enterprise database and native console.

No connection, migration, account mutation or task startup happens during construction.
The enclosing runtime owns the shared SQLAlchemy engine. Per-request native credentials
are supplied by the authenticated HTTP layer, never persisted in this configuration.
"""

from dataclasses import dataclass

from pydantic import Field
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.adapters.workbench_context_client import DifyWorkbenchContextClient
from enterprise_platform.adapters.workbench_dispatch import WorkbenchChatDispatcher
from enterprise_platform.application.contracts import Contract
from enterprise_platform.application.workbench_branch_service import WorkbenchBranchService
from enterprise_platform.application.workbench_service import WorkbenchSendAuthority, WorkbenchSendService
from enterprise_platform.persistence.workbench_branch_repository import SqlAlchemyBranchRepository
from enterprise_platform.persistence.workbench_repository import SqlAlchemyMessageIntentRepository


class WorkbenchConfiguration(Contract):
    preflight_timeout_seconds: int = Field(default=30, strict=True, ge=1, le=120)
    generation_timeout_seconds: int = Field(default=300, strict=True, ge=1, le=1800)
    max_response_bytes: int = Field(default=1048576, strict=True, ge=1024, le=2097152)


@dataclass(frozen=True)
class WorkbenchRuntime:
    configuration: WorkbenchConfiguration
    dispatcher: WorkbenchChatDispatcher
    messages: SqlAlchemyMessageIntentRepository
    branches: SqlAlchemyBranchRepository
    branch_service: WorkbenchBranchService


def create_workbench(
    configuration: WorkbenchConfiguration,
    *,
    sessions: sessionmaker[Session],
    console_url: str,
) -> WorkbenchRuntime:
    messages = SqlAlchemyMessageIntentRepository(sessions)
    branches = SqlAlchemyBranchRepository(sessions)
    checks = DifyWorkbenchContextClient(
        base_url=console_url,
        timeout_seconds=configuration.preflight_timeout_seconds,
        max_response_bytes=configuration.max_response_bytes,
    )
    transport = DifyWorkbenchContextClient(
        base_url=console_url,
        timeout_seconds=configuration.generation_timeout_seconds,
        max_response_bytes=configuration.max_response_bytes,
    )
    sender = WorkbenchSendService(messages, WorkbenchSendAuthority(branches, checks), checks)
    return WorkbenchRuntime(
        configuration,
        WorkbenchChatDispatcher(sender, messages, transport, completion_reader=checks),
        messages,
        branches,
        WorkbenchBranchService(branches, checks),
    )
