"""数据接入与查询契约；自有SQLite默认可用，其他服务须独立核验目标与范围。"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr


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
    rules: list["QualityRule"] = Field(default_factory=list, max_length=50)


class ProcessingInput(Input):
    name: str = Field(min_length=1, max_length=160)
    query_id: str
    operations: list["ProcessingOperation"] = Field(min_length=1, max_length=20)


class MetadataResource(Input):
    name: str = Field(min_length=1, max_length=160)
    datasource_id: str | None = None
    sql: str | None = Field(default=None, max_length=30000)
    parameters: dict = Field(default_factory=dict)
    definition: dict = Field(default_factory=dict)
    revision: int | None = Field(default=None, ge=1)


RowScalar = StrictStr | StrictInt | StrictFloat | StrictBool | None


class RowCondition(Input):
    field: str = Field(min_length=1, max_length=160)
    operator: Literal["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "is_null", "not_null"]
    value_source: Literal["literal", "current_user_id", "current_workspace_id"] = "literal"
    value: RowScalar | list[RowScalar] = None


class RowRule(Input):
    subject_type: Literal["all", "role", "user"]
    subject_ids: list[str] = Field(default_factory=list, max_length=100)
    conditions: list[RowCondition] = Field(min_length=1, max_length=20)


class RowPolicyInput(Input):
    table_name: str = Field(min_length=1, max_length=160)
    mode: Literal["all", "rules"]
    rules: list[RowRule] = Field(default_factory=list, max_length=50)


class RowPolicyPatch(Input):
    revision: int = Field(ge=1)
    mode: Literal["all", "rules"]
    rules: list[RowRule] = Field(default_factory=list, max_length=50)


DataType = Literal["text", "decimal", "integer", "boolean", "date", "datetime"]


class DataCondition(Input):
    field: str = Field(min_length=1, max_length=160)
    operator: Literal["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "contains", "is_null", "not_null"]
    value: RowScalar | list[RowScalar] = None
    data_type: DataType = "text"


class Aggregation(Input):
    name: str = Field(default="value", min_length=1, max_length=160)
    function: Literal["sum", "avg", "min", "max", "count", "count_distinct"]
    field: str | None = Field(default=None, min_length=1, max_length=160)
    data_type: Literal["decimal", "text"] = "decimal"
    unit_field: str | None = Field(default=None, min_length=1, max_length=160)


class FieldOperand(Input):
    field: str = Field(min_length=1, max_length=160)


class LiteralOperand(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    value: RowScalar


class DerivedExpression(Input):
    op: Literal["add", "subtract", "multiply", "divide", "coalesce", "concat", "round"]
    args: list[Union[FieldOperand, LiteralOperand, "DerivedExpression"]] = Field(min_length=1, max_length=10)
    digits: int | None = Field(default=None, ge=0, le=18)


class SelectOperation(Input):
    type: Literal["select"]
    fields: list[str] = Field(min_length=1, max_length=100)


class DropNullOperation(Input):
    type: Literal["drop_null"]
    field: str


class DeduplicateOperation(Input):
    type: Literal["deduplicate"]
    fields: list[str] | None = Field(default=None, min_length=1, max_length=100)


class FilterOperation(Input):
    type: Literal["filter"]
    match: Literal["all", "any"] = "all"
    conditions: list[DataCondition] = Field(min_length=1, max_length=20)


class RenameOperation(Input):
    type: Literal["rename"]
    field: str
    output_field: str = Field(min_length=1, max_length=160)


class CastOperation(Input):
    type: Literal["cast"]
    field: str
    output_field: str | None = Field(default=None, min_length=1, max_length=160)
    data_type: DataType
    on_error: Literal["fail", "null"] = "fail"


class DeriveOperation(Input):
    type: Literal["derive"]
    output_field: str = Field(min_length=1, max_length=160)
    expression: DerivedExpression
    on_error: Literal["fail", "null"] = "fail"


class AggregateOperation(Input):
    type: Literal["aggregate"]
    group_by: list[str] = Field(default_factory=list, max_length=10)
    aggregations: list[Aggregation] = Field(min_length=1, max_length=20)


ProcessingOperation = Annotated[Union[SelectOperation, DropNullOperation, DeduplicateOperation, FilterOperation, RenameOperation, CastOperation, DeriveOperation, AggregateOperation], Field(discriminator="type")]


class QualityRule(Input):
    name: str = Field(default="", max_length=160)
    type: Literal["required", "unique", "range", "enum", "type", "length", "compare"]
    field: str = Field(min_length=1, max_length=160)
    data_type: DataType = "text"
    allow_missing: bool = True
    minimum: RowScalar = None
    maximum: RowScalar = None
    values: list[RowScalar] = Field(default_factory=list, max_length=100)
    min_length: int | None = Field(default=None, ge=0, le=10000)
    max_length: int | None = Field(default=None, ge=0, le=10000)
    other_field: str | None = Field(default=None, min_length=1, max_length=160)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"] = "eq"


class MetricDefinition(Input):
    version: Literal[1]
    kind: Literal["aggregate", "ratio"]
    group_by: list[str] = Field(default_factory=list, max_length=10)
    aggregation: Aggregation | None = None
    numerator: Aggregation | None = None
    denominator: Aggregation | None = None
    scale: RowScalar = "1"
    unit: str = Field(default="", max_length=80)


class MetricExecutionInput(Input):
    query_id: str
    revision: int | None = Field(default=None, ge=1)


QualityInput.model_rebuild()
ProcessingInput.model_rebuild()
