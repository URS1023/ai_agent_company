"""数据接入与查询契约；自有SQLite默认可用，其他服务须独立核验目标与范围。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConnectionConfig(Input):
    host: str = Field(default="", max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str = Field(default="", max_length=160)
    schema_name: str = Field(default="public", max_length=100)
    username: str = Field(default="", max_length=160)
    tls: bool = True
    endpoint_path: str = Field(default="/data", max_length=500)
    response_key: str = Field(default="", max_length=100)


class SourceInput(Input):
    name: str = Field(min_length=1, max_length=160)
    provider: Literal["sqlite", "postgres", "mysql", "http_json", "elasticsearch"] = "sqlite"
    description: str = Field(default="", max_length=2000)
    max_rows: int = Field(default=1000, ge=1, le=10000)
    revision: int | None = Field(default=None, ge=1)
    connection_config: ConnectionConfig | None = None
    credential_ref: str | None = None
    schema_snapshot: list[dict] = Field(default_factory=list, max_length=100)


class SourcePatch(Input):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    max_rows: int | None = Field(default=None, ge=1, le=10000)
    field_roles: dict[str, dict[str, list[str]]] | None = None
    connection_config: ConnectionConfig | None = None
    credential_ref: str | None = None
    schema_snapshot: list[dict] | None = Field(default=None, max_length=100)


class TargetApproval(Input):
    revision: int = Field(ge=1)
    host: str
    port: int = Field(ge=1, le=65535)
    database: str
    service_owner: str = Field(min_length=3, max_length=300)
    expires_at: str
    scope: list[Literal["metadata", "query"]] = Field(min_length=1, max_length=2)
    note: str = Field(min_length=5, max_length=2000)
    resume_after_failure: bool = False


class SourceRecovery(Input):
    revision: int = Field(ge=1)
    note: str = Field(min_length=5, max_length=2000)


class DatasetInput(Input):
    name: str = Field(min_length=1, max_length=160)
    datasource_id: str | None = None
    csv_text: str = Field(min_length=1, max_length=5_000_000)
    description: str = Field(default="", max_length=2000)


class DatasetPatch(Input):
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


class QueryInput(Input):
    datasource_id: str
    sql: str = Field(min_length=1, max_length=30000)
    parameters: dict = Field(default_factory=dict)
    max_rows: int = Field(default=1000, ge=1, le=10000)
    name: str = Field(default="数据查询", max_length=160)


class NaturalQueryInput(Input):
    datasource_id: str
    question: str = Field(min_length=2, max_length=2000)
    model_profile_id: str
    max_rows: int = Field(default=1000, ge=1, le=10000)
    execute: bool = True


class MetadataInput(Input):
    revision: int | None = Field(default=None, ge=1)


class QualityInput(Input):
    query_id: str
    required_fields: list[str] = Field(default_factory=list, max_length=100)
    unique_fields: list[str] = Field(default_factory=list, max_length=100)


class ProcessingInput(Input):
    name: str = Field(min_length=1, max_length=160)
    query_id: str
    operations: list[dict] = Field(min_length=1, max_length=20)


class MetadataResource(Input):
    name: str = Field(min_length=1, max_length=160)
    datasource_id: str | None = None
    sql: str | None = Field(default=None, max_length=30000)
    parameters: dict = Field(default_factory=dict)
    definition: dict = Field(default_factory=dict)
    revision: int | None = Field(default=None, ge=1)
