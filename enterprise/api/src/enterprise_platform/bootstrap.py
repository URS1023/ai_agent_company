"""Compose the separate enterprise service without starting it or changing a schema.

Only explicit ENTERPRISE_* settings are read. The native Dify database is never a
fallback. Operators apply the independently reviewed migration before deployment;
application construction does not connect, create tables, or mutate accounts.
"""

import os
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from fastapi import FastAPI
from pydantic import ConfigDict, Field, SecretStr, TypeAdapter, ValidationError, field_validator, model_validator
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import sessionmaker

from enterprise_platform.adapters.dify_identity import DifyIdentityClient
from enterprise_platform.adapters.source_encryption import AesGcmSourceCipher, SourceKeyring
from enterprise_platform.adapters.workflow_credential_encryption import AesGcmCredentialCipher, CredentialKeyring
from enterprise_platform.adapters.workflow_draft_client import DifyWorkflowDraftClient
from enterprise_platform.adapters.workflow_draft_read_client import DifyWorkflowDraftReadClient
from enterprise_platform.adapters.workflow_gateway import NativeWorkflowGateway, WorkflowCredential
from enterprise_platform.adapters.workflow_plugin_client_factory import ProfiledWorkflowPluginClientFactory
from enterprise_platform.adapters.workflow_publication_read_client import DifyWorkflowPublicationReadClient
from enterprise_platform.adapters.workflow_publish_client import DifyWorkflowPublishClient
from enterprise_platform.adapters.workflow_setup_client import DifyWorkflowSetupClient
from enterprise_platform.adapters.workflow_token_client import DifyWorkflowTokenClient
from enterprise_platform.application.assessments import AssessmentService, ImmutableSpecificationCatalog, Specification
from enterprise_platform.application.contracts import Contract
from enterprise_platform.application.dispatcher import RunDispatcher
from enterprise_platform.application.input_capture import (
    ImmutableReadCatalog,
    InputCaptureService,
    RegisteredRead,
    RegisteredReadRegistry,
    RegisteredSourceReader,
)
from enterprise_platform.application.managed_execution import (
    ExecutionAuthenticator,
    ExecutionKey,
    ManagedExecutionService,
)
from enterprise_platform.application.native_registration import NativeRegistrationService, validate_registration_token
from enterprise_platform.application.service import BusinessService
from enterprise_platform.application.source_registration import EndpointPolicy, SourceEndpoint
from enterprise_platform.application.source_service import SourceService, StoredReadCatalog
from enterprise_platform.application.workflow_activation_service import WorkflowActivationService
from enterprise_platform.application.workflow_enrollment_execution import WorkflowEnrollmentExecutor
from enterprise_platform.application.workflow_enrollment_service import WorkflowEnrollmentService
from enterprise_platform.application.workflow_plugin_profiles import (
    PluginCredentialProfile,
    WorkflowPluginProfileRegistry,
)
from enterprise_platform.application.workflow_provisioning_execution import WorkflowProvisioningExecutor
from enterprise_platform.application.workflow_provisioning_service import WorkflowProvisioningService
from enterprise_platform.application.workflow_setup_service import WorkflowSetupService
from enterprise_platform.dashboard_runtime import DashboardConfiguration, DashboardRuntime, create_dashboards
from enterprise_platform.http.app import create_app
from enterprise_platform.http.internal import create_managed_router
from enterprise_platform.http.native_registration import create_registration_router
from enterprise_platform.persistence.execution_lookup import SqlAlchemyExecutionLookup
from enterprise_platform.persistence.repository import SqlAlchemyRepository
from enterprise_platform.persistence.sources import SqlAlchemySourceRepository
from enterprise_platform.persistence.workflow_activation_lookup import SqlAlchemyActiveExecutionKeyLookup
from enterprise_platform.persistence.workflow_activation_repository import SqlAlchemyWorkflowActivationRepository
from enterprise_platform.persistence.workflow_credentials import SqlAlchemyWorkflowCredentialVault
from enterprise_platform.persistence.workflow_enrollment_repository import SqlAlchemyWorkflowEnrollmentRepository
from enterprise_platform.persistence.workflow_provisioning import SqlAlchemyWorkflowProvisioningRepository
from enterprise_platform.persistence.workflow_setups import SqlAlchemyWorkflowSetupRepository
from enterprise_platform.scheduler_runtime import SchedulerConfiguration, SchedulerRuntime, create_scheduler
from enterprise_platform.workbench_runtime import WorkbenchConfiguration, WorkbenchRuntime, create_workbench


class Settings(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    database_url: SecretStr = Field(repr=False)
    dify_console_url: str
    dify_service_url: str
    allowed_origins: tuple[str, ...] = Field(min_length=1)
    workflow_credentials: tuple[WorkflowCredential, ...] = Field(default=(), repr=False)
    registered_reads: tuple[RegisteredRead, ...] = Field(default=(), repr=False)
    managed_execution_keys: tuple[ExecutionKey, ...] = Field(default=(), repr=False)
    specifications: tuple[Specification, ...] = Field(default=(), repr=False)
    source_keyring: SourceKeyring | None = Field(default=None, repr=False)
    source_endpoints: tuple[SourceEndpoint, ...] = Field(default=(), max_length=1000)
    workflow_setup_enabled: bool = Field(default=False, strict=True)
    workflow_provisioning_enabled: bool = Field(default=False, strict=True)
    workflow_enrollment_enabled: bool = Field(default=False, strict=True)
    workflow_activation_enabled: bool = Field(default=False, strict=True)
    scheduler: SchedulerConfiguration | None = None
    dashboards: DashboardConfiguration | None = None
    workbench: WorkbenchConfiguration | None = None
    native_registration_token: SecretStr | None = Field(default=None, repr=False, exclude=True)
    workflow_plugin_profiles: tuple[PluginCredentialProfile, ...] = Field(default=(), repr=False, exclude=True)
    workflow_credential_keyring: CredentialKeyring | None = Field(default=None, repr=False, exclude=True)

    @model_validator(mode="after")
    def exclusive_workflow_credentials(self) -> Self:
        if self.workflow_credential_keyring is not None and self.workflow_credentials:
            raise ValueError("Choose static workflow credentials or the encrypted vault, not both")
        return self

    @model_validator(mode="after")
    def provisioning_prerequisites(self) -> Self:
        if self.scheduler is not None and not self.workflow_activation_enabled:
            raise ValueError("Scheduling requires workflow activation")
        if self.native_registration_token is not None:
            validate_registration_token(self.native_registration_token)
            if not self.workflow_activation_enabled:
                raise ValueError("Native registration requires workflow activation")
        if self.workflow_activation_enabled and (not self.workflow_enrollment_enabled or not self.specifications):
            raise ValueError("Workflow activation requires enrollment and a specification catalog")
        if self.workflow_enrollment_enabled and (
            not self.workflow_provisioning_enabled or self.workflow_credential_keyring is None
        ):
            raise ValueError("Workflow enrollment requires provisioning and an encrypted credential vault")
        if self.workflow_provisioning_enabled and (
            not self.workflow_setup_enabled or not self.workflow_plugin_profiles
        ):
            raise ValueError("Workflow provisioning requires setup and explicit server plugin profiles")
        WorkflowPluginProfileRegistry(self.workflow_plugin_profiles)
        return self

    @field_validator("database_url")
    @classmethod
    def dedicated_database(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except ArgumentError:
            raise ValueError("Invalid enterprise database configuration") from None
        if (
            url.drivername != "postgresql+psycopg"
            or not re.fullmatch(r"enterprise_[a-z0-9_]{1,48}", url.database or "")
            or not url.host
            or not url.username
            or set(url.query) - {"sslmode", "sslrootcert", "sslcert", "sslkey"}
        ):
            raise ValueError("Use a dedicated enterprise_* PostgreSQL database with the psycopg driver")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def exact_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for origin in value:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path
                or parsed.port == 0
                or "*" in origin
                or origin != origin.strip()
            ):
                raise ValueError("Origins must be exact HTTP origins without paths or credentials")
        if len(set(value)) != len(value):
            raise ValueError("Duplicate allowed origin")
        return value

    @field_validator("dify_console_url", "dify_service_url")
    @classmethod
    def fixed_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.port == 0
        ):
            raise ValueError("Use a fixed server-owned native Dify endpoint")
        return value

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environment is None else environment

        def required(name: str) -> str:
            value = env.get(name, "").strip()
            if not value:
                raise ValueError(f"Missing required setting: {name}")
            return value

        credentials: tuple[WorkflowCredential, ...] = ()
        path = env.get("ENTERPRISE_WORKFLOW_CREDENTIALS_FILE")
        if path:
            credentials = read_configuration(
                path, "ENTERPRISE_WORKFLOW_CREDENTIALS_FILE", TypeAdapter(tuple[WorkflowCredential, ...])
            )
        reads: tuple[RegisteredRead, ...] = ()
        read_path = env.get("ENTERPRISE_READ_CATALOG_FILE")
        if read_path:
            reads = read_configuration(
                read_path, "ENTERPRISE_READ_CATALOG_FILE", TypeAdapter(tuple[RegisteredRead, ...])
            )
        node_keys: tuple[ExecutionKey, ...] = ()
        if key_path := env.get("ENTERPRISE_MANAGED_EXECUTION_KEYS_FILE"):
            node_keys = read_configuration(
                key_path, "ENTERPRISE_MANAGED_EXECUTION_KEYS_FILE", TypeAdapter(tuple[ExecutionKey, ...])
            )
        specifications: tuple[Specification, ...] = ()
        if specification_path := env.get("ENTERPRISE_SPECIFICATIONS_FILE"):
            specifications = read_configuration(
                specification_path, "ENTERPRISE_SPECIFICATIONS_FILE", TypeAdapter(tuple[Specification, ...])
            )
        setup_enabled = env.get("ENTERPRISE_WORKFLOW_SETUP_ENABLED", "false").strip().lower()
        if setup_enabled not in {"true", "false"}:
            raise ValueError("ENTERPRISE_WORKFLOW_SETUP_ENABLED must be true or false")
        provisioning_enabled = env.get("ENTERPRISE_WORKFLOW_PROVISIONING_ENABLED", "false").strip().lower()
        if provisioning_enabled not in {"true", "false"}:
            raise ValueError("ENTERPRISE_WORKFLOW_PROVISIONING_ENABLED must be true or false")
        enrollment_enabled = env.get("ENTERPRISE_WORKFLOW_ENROLLMENT_ENABLED", "false").strip().lower()
        if enrollment_enabled not in {"true", "false"}:
            raise ValueError("ENTERPRISE_WORKFLOW_ENROLLMENT_ENABLED must be true or false")
        activation_enabled = env.get("ENTERPRISE_WORKFLOW_ACTIVATION_ENABLED", "false").strip().lower()
        if activation_enabled not in {"true", "false"}:
            raise ValueError("ENTERPRISE_WORKFLOW_ACTIVATION_ENABLED must be true or false")
        profiles: tuple[PluginCredentialProfile, ...] = ()
        if profile_path := env.get("ENTERPRISE_WORKFLOW_PLUGIN_PROFILES_FILE"):
            profiles = read_configuration(
                profile_path,
                "ENTERPRISE_WORKFLOW_PLUGIN_PROFILES_FILE",
                TypeAdapter(tuple[PluginCredentialProfile, ...]),
            )
        return cls(
            database_url=SecretStr(required("ENTERPRISE_DATABASE_URL")),
            dify_console_url=required("ENTERPRISE_DIFY_CONSOLE_URL"),
            dify_service_url=required("ENTERPRISE_DIFY_SERVICE_URL"),
            allowed_origins=tuple(part.strip() for part in required("ENTERPRISE_ALLOWED_ORIGINS").split(",")),
            workflow_credentials=credentials,
            registered_reads=reads,
            managed_execution_keys=node_keys,
            specifications=specifications,
            workflow_setup_enabled=setup_enabled == "true",
            workflow_provisioning_enabled=provisioning_enabled == "true",
            workflow_enrollment_enabled=enrollment_enabled == "true",
            workflow_activation_enabled=activation_enabled == "true",
            scheduler=(
                read_inline_configuration(
                    env["ENTERPRISE_SCHEDULER_JSON"], "ENTERPRISE_SCHEDULER_JSON", TypeAdapter(SchedulerConfiguration)
                )
                if "ENTERPRISE_SCHEDULER_JSON" in env
                else None
            ),
            dashboards=(
                read_inline_configuration(
                    env["ENTERPRISE_DASHBOARD_JSON"], "ENTERPRISE_DASHBOARD_JSON", TypeAdapter(DashboardConfiguration)
                )
                if "ENTERPRISE_DASHBOARD_JSON" in env
                else None
            ),
            native_registration_token=(
                SecretStr(env["ENTERPRISE_NATIVE_REGISTRATION_TOKEN"])
                if "ENTERPRISE_NATIVE_REGISTRATION_TOKEN" in env
                else None
            ),
            workbench=(
                read_inline_configuration(
                    env["ENTERPRISE_WORKBENCH_JSON"], "ENTERPRISE_WORKBENCH_JSON", TypeAdapter(WorkbenchConfiguration)
                )
                if "ENTERPRISE_WORKBENCH_JSON" in env
                else None
            ),
            workflow_plugin_profiles=profiles,
            workflow_credential_keyring=(
                read_inline_configuration(
                    env["ENTERPRISE_WORKFLOW_CREDENTIAL_KEYS_JSON"],
                    "ENTERPRISE_WORKFLOW_CREDENTIAL_KEYS_JSON",
                    TypeAdapter(CredentialKeyring),
                )
                if env.get("ENTERPRISE_WORKFLOW_CREDENTIAL_KEYS_JSON")
                else None
            ),
            source_keyring=(
                read_inline_configuration(
                    env["ENTERPRISE_SOURCE_ENCRYPTION_KEYS_JSON"],
                    "ENTERPRISE_SOURCE_ENCRYPTION_KEYS_JSON",
                    TypeAdapter(SourceKeyring),
                )
                if env.get("ENTERPRISE_SOURCE_ENCRYPTION_KEYS_JSON")
                else None
            ),
            source_endpoints=(
                read_inline_configuration(
                    env["ENTERPRISE_SOURCE_ALLOWED_ENDPOINTS_JSON"],
                    "ENTERPRISE_SOURCE_ALLOWED_ENDPOINTS_JSON",
                    TypeAdapter(tuple[SourceEndpoint, ...]),
                )
                if env.get("ENTERPRISE_SOURCE_ALLOWED_ENDPOINTS_JSON")
                else ()
            ),
        )


def read_inline_configuration[T](value: str, setting: str, adapter: TypeAdapter[T]) -> T:
    try:
        if len(value.encode("utf-8")) > 65536:
            raise ValueError()
        return adapter.validate_json(value)
    except ValueError:
        raise ValueError(f"Invalid {setting}") from None


def read_configuration[T](path: str, setting: str, adapter: TypeAdapter[T]) -> T:
    """Read a bounded operator-mounted configuration without echoing secret content."""
    try:
        with Path(path).open("rb") as source:
            content = source.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("Configuration exceeds limit")
        return adapter.validate_json(content)
    except (OSError, ValueError, ValidationError):
        raise ValueError(f"Invalid {setting}") from None


@dataclass(frozen=True)
class Runtime:
    app: FastAPI
    engine: Engine
    repository: SqlAlchemyRepository
    dispatcher: RunDispatcher
    input_capture: InputCaptureService
    managed_execution: ManagedExecutionService | None = None
    workflow_credential_vault: SqlAlchemyWorkflowCredentialVault | None = None
    workflow_provisioning: WorkflowProvisioningService | None = None
    workflow_enrollment: WorkflowEnrollmentService | None = None
    workflow_activation: WorkflowActivationService | None = None
    scheduler: SchedulerRuntime | None = None
    dashboards: DashboardRuntime | None = None
    workbench: WorkbenchRuntime | None = None

    def close(self) -> None:
        self.engine.dispose()


def create_runtime(settings: Settings) -> Runtime:
    plugin_clients = (
        ProfiledWorkflowPluginClientFactory(
            base_url=settings.dify_console_url, profiles=settings.workflow_plugin_profiles
        )
        if settings.workflow_provisioning_enabled
        else None
    )
    identity = DifyIdentityClient(base_url=settings.dify_console_url)
    gateway = NativeWorkflowGateway(base_url=settings.dify_service_url, credentials=settings.workflow_credentials)
    static_registry = ImmutableReadCatalog(settings.registered_reads)
    cipher = AesGcmSourceCipher(settings.source_keyring) if settings.source_keyring is not None else None
    credential_cipher = (
        AesGcmCredentialCipher(settings.workflow_credential_keyring)
        if settings.workflow_credential_keyring is not None
        else None
    )
    endpoints = EndpointPolicy(settings.source_endpoints)
    specifications = ImmutableSpecificationCatalog(settings.specifications)
    authenticator = ExecutionAuthenticator(settings.managed_execution_keys)
    if settings.managed_execution_keys and not settings.specifications:
        raise ValueError("Managed execution requires a specification catalog")
    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        hide_parameters=True,
        connect_args={"options": "-c search_path=public", "connect_timeout": 5},
    ).execution_options(schema_translate_map={None: "public"})
    sessions = sessionmaker(engine, expire_on_commit=False)
    credential_vault = (
        SqlAlchemyWorkflowCredentialVault(sessions, credential_cipher) if credential_cipher is not None else None
    )
    if credential_vault is not None:
        gateway = NativeWorkflowGateway(base_url=settings.dify_service_url, credential_resolver=credential_vault)
    repository = SqlAlchemyRepository(sessions)
    service = BusinessService(repository)
    sources_repository = SqlAlchemySourceRepository(sessions)
    sources = SourceService(sources_repository, repository, cipher=cipher, endpoints=endpoints)
    workflow_setups = (
        WorkflowSetupService(
            SqlAlchemyWorkflowSetupRepository(sessions),
            service,
            sources,
            DifyWorkflowSetupClient(base_url=settings.dify_console_url),
        )
        if settings.workflow_setup_enabled
        else None
    )
    workflow_provisioning = (
        WorkflowProvisioningService(
            SqlAlchemyWorkflowProvisioningRepository(sessions),
            service,
            workflow_setups,
            WorkflowProvisioningExecutor(
                reader=DifyWorkflowDraftReadClient(base_url=settings.dify_console_url),
                plugin_clients=plugin_clients,
                binder=DifyWorkflowDraftClient(base_url=settings.dify_console_url),
                publisher=DifyWorkflowPublishClient(base_url=settings.dify_console_url),
            ),
            profiles=WorkflowPluginProfileRegistry(settings.workflow_plugin_profiles),
        )
        if plugin_clients is not None and workflow_setups is not None
        else None
    )
    workflow_enrollment = (
        WorkflowEnrollmentService(
            SqlAlchemyWorkflowEnrollmentRepository(sessions, credential_cipher),
            service,
            workflow_provisioning,
            WorkflowEnrollmentExecutor(
                reader=DifyWorkflowPublicationReadClient(base_url=settings.dify_console_url),
                token_issuer=DifyWorkflowTokenClient(base_url=settings.dify_console_url),
            ),
        )
        if settings.workflow_enrollment_enabled and credential_cipher is not None and workflow_provisioning is not None
        else None
    )
    profiles = WorkflowPluginProfileRegistry(settings.workflow_plugin_profiles)
    workflow_activation = (
        WorkflowActivationService(
            SqlAlchemyWorkflowActivationRepository(sessions, profiles, specifications),
            SqlAlchemyWorkflowEnrollmentRepository(sessions, credential_cipher),
            business=service,
            profiles=profiles,
            specifications=specifications,
        )
        if settings.workflow_activation_enabled and credential_cipher is not None
        else None
    )
    if workflow_activation is not None:
        authenticator = ExecutionAuthenticator(
            settings.managed_execution_keys,
            active_keys=SqlAlchemyActiveExecutionKeyLookup(sessions, profiles),
        )
    registry: RegisteredReadRegistry = (
        StoredReadCatalog(sources_repository, cipher, endpoints, static_registry)
        if cipher is not None
        else static_registry
    )
    capture = InputCaptureService(repository, registry, RegisteredSourceReader())
    managed: ManagedExecutionService | None = None
    try:
        workbench = (
            create_workbench(settings.workbench, sessions=sessions, console_url=settings.dify_console_url)
            if settings.workbench is not None
            else None
        )
        scheduler = (
            create_scheduler(
                settings.scheduler,
                sessions=sessions,
                business=service,
                reads=registry,
                profiles=profiles,
                console_url=settings.dify_console_url,
            )
            if settings.scheduler is not None
            else None
        )
        dashboards = (
            create_dashboards(
                settings.dashboards,
                sessions=sessions,
                reads=registry,
                devices=repository,
                sources=sources_repository,
                credentials=credential_vault,
                native_service_url=settings.dify_service_url,
            )
            if settings.dashboards is not None
            else None
        )
        app = create_app(
            service,
            identity,
            allowed_origins=settings.allowed_origins,
            sources=sources,
            workflow_setups=workflow_setups,
            workflow_provisioning=workflow_provisioning,
            workflow_enrollment=workflow_enrollment,
            workflow_activation=workflow_activation,
            schedules=scheduler.service if scheduler is not None else None,
            dashboards=dashboards.service if dashboards is not None else None,
            workbench=workbench.dispatcher if workbench is not None else None,
            workbench_branches=workbench.branch_service if workbench is not None else None,
        )
        if settings.managed_execution_keys or workflow_activation is not None:
            managed = ManagedExecutionService(
                authenticator,
                SqlAlchemyExecutionLookup(sessions),
                repository,
                capture,
                AssessmentService(specifications),
            )
            app.include_router(create_managed_router(managed))
        if workflow_activation is not None and settings.native_registration_token is not None:
            app.include_router(
                create_registration_router(
                    NativeRegistrationService(
                        SqlAlchemyActiveExecutionKeyLookup(sessions, profiles), settings.native_registration_token
                    )
                )
            )
    except Exception:
        engine.dispose()
        raise
    return Runtime(
        app,
        engine,
        repository,
        RunDispatcher(repository, gateway),
        capture,
        managed,
        credential_vault,
        workflow_provisioning,
        workflow_enrollment,
        workflow_activation,
        scheduler,
        dashboards,
        workbench,
    )


def create_application() -> FastAPI:
    """ASGI factory; deployment invokes it explicitly, importing this module does not."""
    runtime = create_runtime(Settings.from_environment())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            runtime.close()

    runtime.app.router.lifespan_context = lifespan
    return runtime.app
