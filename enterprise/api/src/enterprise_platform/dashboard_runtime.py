"""Explicit dashboard composition; construction never reads files or applies schema."""

from dataclasses import dataclass
from pathlib import Path
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.adapters.dashboard_query_approvals import FileDashboardQueryApprovals
from enterprise_platform.adapters.dashboard_schema_grants import FileDashboardSchemaGrants
from enterprise_platform.adapters.dashboard_sql_generator import NativeSqlDraftGenerator
from enterprise_platform.adapters.dashboard_sql_sample_policies import FileSqlSamplePolicies
from enterprise_platform.adapters.dashboard_sql_trial_grants import FileSqlTrialGrants
from enterprise_platform.adapters.dashboard_templates import FileDashboardTemplates
from enterprise_platform.adapters.dify_workflows import PinnedWorkflowTarget
from enterprise_platform.application.contracts import Contract, Identifier
from enterprise_platform.application.dashboard_binding_service import DashboardBindingService
from enterprise_platform.application.dashboard_query_execution import DeviceDashboardQueryExecutor
from enterprise_platform.application.dashboard_query_registry import RegisteredDeviceDashboardQueries
from enterprise_platform.application.dashboard_refresh_service import DashboardRefreshService
from enterprise_platform.application.dashboard_schema_resolver import RegisteredSqlSchemaResolver
from enterprise_platform.application.dashboard_service import DashboardService
from enterprise_platform.application.dashboard_sql_generation import DashboardSqlGeneration
from enterprise_platform.application.dashboard_sql_trial import DashboardSqlTrial
from enterprise_platform.application.dashboard_sql_trial_authorization import RegisteredSqlTrialAuthorizer
from enterprise_platform.application.dashboard_sql_trial_executor import RegisteredSqlTrialExecutor
from enterprise_platform.application.input_capture import RegisteredReadRegistry, RegisteredSourceReader
from enterprise_platform.application.source_ports import SourceRepository
from enterprise_platform.application.source_service import DeviceLookup
from enterprise_platform.application.workflow_credential_ports import WorkflowCredentialResolver
from enterprise_platform.persistence.dashboard_sql_drafts import SqlAlchemySqlDraftRepository
from enterprise_platform.persistence.dashboard_sql_trial_evidence import SqlAlchemySqlTrialEvidenceRepository
from enterprise_platform.persistence.dashboards import SqlAlchemyDashboardRepository


class TemplateCatalogConfiguration(Contract):
    file: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def absolute_file(self) -> Self:
        if not self.file.is_absolute():
            raise ValueError("Template catalog requires an absolute path")
        return self


class SqlGenerationConfiguration(Contract):
    grants_file: Path
    workspace_id: Identifier
    app_id: Identifier
    workflow_id: UUID
    secret_ref: Identifier

    @model_validator(mode="after")
    def explicit_grants_file(self) -> Self:
        if not self.grants_file.is_absolute():
            raise ValueError("SQL generation requires an absolute grant file")
        return self


class SqlTrialConfiguration(Contract):
    grants_file: Path
    schema_grants_file: Path
    sample_policies_file: Path | None = None

    @model_validator(mode="after")
    def separate_authority_files(self) -> Self:
        if not self.grants_file.is_absolute() or not self.schema_grants_file.is_absolute():
            raise ValueError("SQL trials require absolute authority files")
        if self.grants_file == self.schema_grants_file:
            raise ValueError("SQL trial and metadata authority files must be distinct")
        if self.sample_policies_file is not None:
            if not self.sample_policies_file.is_absolute():
                raise ValueError("SQL sample policies require an absolute file")
            if self.sample_policies_file in (self.grants_file, self.schema_grants_file):
                raise ValueError("SQL sample policies must be distinct from authority files")
        return self


class DashboardConfiguration(Contract):
    approvals_file: Path
    templates: TemplateCatalogConfiguration | None = None
    sql_generation: SqlGenerationConfiguration | None = None
    sql_trial: SqlTrialConfiguration | None = None

    @model_validator(mode="after")
    def explicit_authority_file(self) -> Self:
        if not self.approvals_file.is_absolute():
            raise ValueError("Dashboard approval configuration requires an absolute path")
        return self


@dataclass(frozen=True)
class DashboardRuntime:
    repository: SqlAlchemyDashboardRepository
    queries: DeviceDashboardQueryExecutor
    service: DashboardService
    bindings: DashboardBindingService


def create_dashboards(
    configuration: DashboardConfiguration,
    *,
    sessions: sessionmaker[Session],
    reads: RegisteredReadRegistry,
    devices: DeviceLookup,
    sources: SourceRepository | None = None,
    credentials: WorkflowCredentialResolver | None = None,
    native_service_url: str | None = None,
) -> DashboardRuntime:
    repository = SqlAlchemyDashboardRepository(sessions)
    registry = RegisteredDeviceDashboardQueries(
        FileDashboardQueryApprovals(configuration.approvals_file), reads, devices
    )
    queries = DeviceDashboardQueryExecutor(registry, RegisteredSourceReader())
    templates = (
        FileDashboardTemplates(configuration.templates.file, configuration.templates.sha256)
        if configuration.templates
        else None
    )
    bindings = DashboardBindingService(repository, registry)
    drafts = SqlAlchemySqlDraftRepository(sessions)
    generation = None
    if configuration.sql_generation is not None:
        if sources is None or credentials is None or native_service_url is None:
            raise ValueError("SQL generation requires source storage, credential vault and native service URL")
        config = configuration.sql_generation
        generation = DashboardSqlGeneration(
            repository,
            RegisteredSqlSchemaResolver(sources, reads, FileDashboardSchemaGrants(config.grants_file)),
            NativeSqlDraftGenerator(
                base_url=native_service_url,
                target=PinnedWorkflowTarget(config.workspace_id, config.app_id, config.workflow_id),
                secret_ref=config.secret_ref,
                credentials=credentials,
            ),
            drafts,
        )
    trial = None
    if configuration.sql_trial is not None:
        if sources is None:
            raise ValueError("SQL trials require source storage")
        trial_config = configuration.sql_trial
        authorizer = RegisteredSqlTrialAuthorizer(FileSqlTrialGrants(trial_config.grants_file))
        trial = DashboardSqlTrial(
            repository,
            drafts,
            RegisteredSqlSchemaResolver(sources, reads, FileDashboardSchemaGrants(trial_config.schema_grants_file)),
            authorizer,
            RegisteredSqlTrialExecutor(sources, reads, drafts, authorizer),
            sample_policies=(
                FileSqlSamplePolicies(trial_config.sample_policies_file)
                if trial_config.sample_policies_file is not None
                else None
            ),
        )
    return DashboardRuntime(
        repository,
        queries,
        DashboardService(
            repository,
            DashboardRefreshService(repository, queries),
            templates=templates,
            binding_service=bindings,
            query_discovery=registry,
            sql_generation=generation,
            sql_trial=trial,
            sql_trial_evidence=SqlAlchemySqlTrialEvidenceRepository(sessions) if trial is not None else None,
        ),
        bindings,
    )
