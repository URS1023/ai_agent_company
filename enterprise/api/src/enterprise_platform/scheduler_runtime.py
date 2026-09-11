"""Explicit scheduler composition sharing enterprise storage and source catalog.

Construction does no I/O or task startup. The owning runtime disposes the shared
engine; loop/report lifetimes are explicit. Native ownership checks are snapshots,
not a distributed lease, so this composition alone is not deployment acceptance.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.adapters.dify_identity import DifyIdentityClient
from enterprise_platform.adapters.schedule_authority import FileScheduleAuthority
from enterprise_platform.adapters.schedule_identity import DifyScheduleIdentity, FileScheduleSession
from enterprise_platform.adapters.schedule_owner_client import DifyScheduleOwnerClient
from enterprise_platform.adapters.schedule_owner_probe import ActivationScheduleOwnerProbe
from enterprise_platform.application.contracts import Contract
from enterprise_platform.application.input_capture import RegisteredReadRegistry
from enterprise_platform.application.schedule_loop import ScheduleLoop, ScheduleLoopPolicy, ScheduleLoopReport
from enterprise_platform.application.schedule_polling import SchedulePoller
from enterprise_platform.application.schedule_service import ScheduleService
from enterprise_platform.application.service import BusinessService
from enterprise_platform.application.workflow_plugin_profiles import WorkflowPluginProfileRegistry
from enterprise_platform.application.workflow_provisioning_contracts import NativeUUID
from enterprise_platform.persistence.schedules import SqlAlchemyScheduleRepository
from enterprise_platform.persistence.workflow_activation_lookup import SqlAlchemyActiveExecutionKeyLookup


class SchedulerConfiguration(Contract):
    workspace_id: NativeUUID
    actor_id: NativeUUID
    session_file: Path
    grants_file: Path
    policy: ScheduleLoopPolicy = Field(default_factory=ScheduleLoopPolicy)

    @model_validator(mode="after")
    def explicit_files(self) -> Self:
        if not self.session_file.is_absolute() or not self.grants_file.is_absolute():
            raise ValueError("Scheduler configuration files require absolute paths")
        if self.session_file == self.grants_file:
            raise ValueError("Scheduler session and grant documents must be separate")
        return self


@dataclass(frozen=True)
class SchedulerRuntime:
    configuration: SchedulerConfiguration
    service: ScheduleService
    poller: SchedulePoller
    identity: DifyScheduleIdentity

    def create_loop(self, report: Callable[[ScheduleLoopReport], Awaitable[None]]) -> ScheduleLoop:
        return ScheduleLoop(
            self.poller,
            self.identity,
            report,
            workspace_id=self.configuration.workspace_id,
            actor_id=self.configuration.actor_id,
            policy=self.configuration.policy,
        )


def create_scheduler(
    configuration: SchedulerConfiguration,
    *,
    sessions: sessionmaker[Session],
    business: BusinessService,
    reads: RegisteredReadRegistry,
    profiles: WorkflowPluginProfileRegistry,
    console_url: str,
) -> SchedulerRuntime:
    configuration = SchedulerConfiguration.model_validate(configuration.model_dump())
    native_identity = DifyIdentityClient(base_url=console_url)
    session_source = FileScheduleSession(configuration.session_file)
    probe = ActivationScheduleOwnerProbe(
        SqlAlchemyActiveExecutionKeyLookup(sessions, profiles),
        native_identity,
        DifyScheduleOwnerClient(base_url=console_url),
        session_source,
    )
    repository = SqlAlchemyScheduleRepository(sessions)
    identity = DifyScheduleIdentity(
        native_identity,
        session_source,
        workspace_id=configuration.workspace_id,
        actor_id=configuration.actor_id,
    )
    service = ScheduleService(
        repository,
        business,
        FileScheduleAuthority(configuration.grants_file, probe),
        reads=reads,
        service_identity=identity,
    )
    return SchedulerRuntime(configuration, service, SchedulePoller(repository, service), identity)
