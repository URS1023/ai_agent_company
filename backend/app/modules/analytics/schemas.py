"""Cloud Brain explicitly documented analysis and data-source contracts."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.common.contracts import ApiModel


# 新建/编辑数据源的入参契约（写模型）。
# 密码是**只写**字段（repr=False，永不回传）；编辑时必须提交 version 以防止覆盖他人
# 修改；allowedTables/allowedColumns 决定授权范围，空列表表示“当前库的全部非系统表”，
# 因此留空等于放开范围，配置页面需要明确提示。
# 注意：本项目不给 Pydantic 模型加类 docstring——pydantic 会把类 docstring 输出为
# JSON Schema 的 description，等于改变对外契约，因此这里用注释说明。
class SourceInput(ApiModel):
    id: str | None = Field(default=None, description="编辑或测试已有数据源时的标识")
    name: str = Field(min_length=1, max_length=200, description="数据源业务名称")
    type: Literal["mysql", "postgres", "sqlite", "elasticsearch"] = Field(default="mysql", description="数据库或搜索引擎类型")
    host: str = Field(default="", max_length=253, description="主机名或 IP；不包含协议、路径和用户名")
    port: int | None = Field(default=None, ge=1, le=65535, description="服务端口；SQLite 不使用")
    database_name: str = Field(default="", alias="databaseName", max_length=200, description="数据库名、受管 SQLite 文件名或 Elasticsearch 索引模式")
    username: str = Field(default="", max_length=200, description="只读数据库账号或搜索引擎用户名")
    password: str | None = Field(default=None, max_length=4096, repr=False, description="只在设置时提交；编辑时留空保留原加密密码，永不回传")
    description: str = Field(default="", max_length=2000, description="业务用途及数据口径说明")
    enabled: bool = Field(default=True, description="是否允许新的查询")
    version: int | None = Field(default=None, ge=1, description="编辑必须提交读取到的配置版本，防止覆盖他人修改")
    ssl: bool = Field(default=False, description="Elasticsearch 使用 HTTPS；证书必须可信")
    allowed_tables: list[str] = Field(default_factory=list, alias="allowedTables", max_length=1000, description="允许访问的表或索引；空列表表示当前数据库的全部非系统表")
    allowed_columns: dict[str, list[str]] = Field(default_factory=dict, alias="allowedColumns", description="按表限定可读字段；未配置表使用其全部字段，空字段列表表示不暴露该表")
    timeout_seconds: int = Field(default=15, alias="timeoutSeconds", ge=1, le=60, description="单次数据库查询超时秒数")
    max_rows: int = Field(default=1000, alias="maxRows", ge=1, le=10000, description="每次查询可返回的最大行数")

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """名称去空格并拒绝纯空白，避免出现界面上不可见的数据源。"""
        if not value.strip():
            raise ValueError("数据源名称不能为空白")
        return value.strip()

    @model_validator(mode="after")
    def validate_connection(self):
        """跨字段校验连接配置：主机、用户名、库名按类型分别必填，主机不得夹带协议/路径/凭据。"""
        if self.type != "sqlite" and not self.host.strip():
            raise ValueError("请输入服务主机地址")
        if self.type in {"mysql", "postgres"} and not self.username.strip():
            raise ValueError("数据库用户名不能为空")
        if self.type != "elasticsearch" and not self.database_name.strip():
            raise ValueError("数据库名不能为空")
        if self.host and any(part in self.host for part in ("/", "\\", "@", "?", "#", "\0")):
            raise ValueError("主机地址不能包含协议、路径或凭据")
        return self


# 数据源的对外视图：只含脱敏信息与元数据状态，不含密码、密钥或连接串。
# 健康状态未检测时为 unknown（不伪造健康）；行数/注释覆盖率等统计来自最近一次元数据
# 同步，未同步时保持 0 与 not_synced。
class SourceView(BaseModel):
    id: str = Field(description="统一智慧云脑 Cloud Brain 数据源标识")
    name: str = Field(description="业务名称")
    type: str = Field(description="提供方类型")
    host: str = Field(description="主机名，不包含凭据")
    port: int | None = Field(description="服务端口")
    databaseName: str = Field(description="数据库或索引名称")
    username: str = Field(description="数据库账号名")
    hasPassword: bool = Field(description="是否已配置加密凭据，不返回密码")
    description: str = Field(description="用途说明")
    configuredDescription: str = Field(default="", description="管理员手工填写的用途说明；空值时界面展示自动说明")
    autoDescription: str = Field(default="", description="根据真实表、字段及数据库注释自动生成的数据范围说明")
    descriptionSource: Literal["configured", "metadata", "empty"] = Field(default="empty", description="当前展示说明的来源")
    environmentSummary: str = Field(default="", description="不含凭据的数据库环境摘要")
    enabled: bool = Field(description="是否启用")
    healthStatus: str = Field(description="unknown/healthy/unhealthy，未检测不伪造健康")
    lastHealthCheckAt: str = Field(default="", description="最近一次真实连接检查时间 UTC；未检查为空")
    healthLatencyMs: int | None = Field(default=None, description="最近一次连接检查耗时毫秒；未检查为 null")
    version: int = Field(description="编辑时必须原样提交的配置版本")
    ssl: bool = Field(description="是否使用 HTTPS")
    allowedTables: list[str] = Field(description="表或索引范围")
    allowedColumns: dict[str, list[str]] = Field(description="字段范围")
    timeoutSeconds: int = Field(description="查询超时秒数")
    maxRows: int = Field(description="返回行上限")
    metadataStatus: Literal["ready", "syncing", "failed", "not_synced", "stale"] = Field(default="not_synced", description="元数据同步状态")
    tableCount: int = Field(default=0, description="同步到的全部授权表/视图数")
    businessTableCount: int = Field(default=0, description="排除系统表后的业务表数")
    fieldCount: int = Field(default=0, description="业务字段数")
    commentedTableCount: int = Field(default=0, description="具有数据库注释的业务表数")
    commentedFieldCount: int = Field(default=0, description="具有数据库注释的业务字段数")
    semanticIndexStatus: Literal["ready", "keyword_only", "failed", "not_indexed"] = Field(default="not_indexed", description="语义表索引状态")
    semanticIndexedTableCount: int = Field(default=0, description="已建立语义文档的业务表数")
    businessDomains: list[str] = Field(default_factory=list, description="根据注释识别的业务域")
    metadataSyncedAt: str = Field(default="", description="最近元数据同步时间 UTC")
    createTime: str = Field(description="创建时间 UTC")
    updateTime: str = Field(description="修改时间 UTC")


class DataSourcePolicyInput(ApiModel):
    """Versioned resource and scheduled-health settings for one source."""

    # version=0 表示首次保存；已有策略必须提交当前版本，否则返回 409。
    version: int = Field(ge=0, description="0 表示首次保存；已有策略必须提交当前版本")
    maxConcurrency: int = Field(default=4, ge=1, le=32, description="同一数据源允许的并发分析查询数")
    dailyQueryLimit: int = Field(default=10000, ge=0, le=1000000, description="UTC 自然日查询尝试上限；0 表示不限")
    healthCheckEnabled: bool = Field(default=False, description="是否由统一调度中心持续执行真实连接检查")
    healthIntervalMinutes: int = Field(default=60, ge=5, le=10080, description="健康检查间隔分钟数，5 分钟到 7 天")


# 数据源治理策略视图：限额配置 + 真实占用 + 调度状态（只读返回）。
class DataSourcePolicyView(BaseModel):
    datasourceId: str
    version: int
    maxConcurrency: int
    dailyQueryLimit: int
    healthCheckEnabled: bool
    healthIntervalMinutes: int
    healthScheduleId: str = ""
    scheduleStatus: str
    nextHealthCheckAt: str = ""
    activeQueries: int = 0
    queriesToday: int = 0


# 单条健康检查记录（不可变证据）：含触发方式、耗时、脱敏说明与错误码。
class DataSourceHealthView(BaseModel):
    id: str
    datasourceId: str
    datasourceVersion: int
    triggerType: Literal["manual", "schedule"]
    status: Literal["healthy", "unhealthy"]
    latencyMs: int
    detail: str
    errorCode: str = ""
    checkedAt: str


class DataSourceConfigurationItem(ApiModel):
    """Portable connection configuration; credentials and platform IDs are excluded."""

    name: str = Field(min_length=1, max_length=200)
    type: Literal["mysql", "postgres", "sqlite", "elasticsearch"]
    host: str = Field(default="", max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    databaseName: str = Field(default="", max_length=200)
    username: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)
    enabled: bool = True
    requiresCredential: bool = Field(default=False, description="源配置曾使用加密凭据；导出文件不包含凭据值")
    ssl: bool = False
    allowedTables: list[str] = Field(default_factory=list, max_length=1000)
    allowedColumns: dict[str, list[str]] = Field(default_factory=dict)
    timeoutSeconds: int = Field(default=15, ge=1, le=60)
    maxRows: int = Field(default=1000, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_portable_connection(self):
        """用普通编辑器的同一套入参校验配置包条目（不含密码字段）。

        抛出：provider/地址/端口/范围不合规时 ``ValueError``（由 Pydantic 转成 422）。
        """
        # Batch import must obey the same provider, address and scope contract
        # as the normal editor, while deliberately having no password field.
        # 批量导入必须复用普通编辑器的 provider/地址/范围校验（但刻意没有密码字段），
        # 因此这里借助 SourceInput 做一次等价校验，避免导入成为绕过校验的通道。
        SourceInput(
            name=self.name, type=self.type, host=self.host, port=self.port,
            databaseName=self.databaseName, username=self.username,
            description=self.description, enabled=self.enabled, ssl=self.ssl,
            allowedTables=self.allowedTables, allowedColumns=self.allowedColumns,
            timeoutSeconds=self.timeoutSeconds, maxRows=self.maxRows,
        )
        return self


# 数据源配置包导入的入参：dryRun=True 时只做校验与预演，不写库。
# conflictMode 决定同名冲突处理（skip/rename/update），update 需要额外编辑权限；
# 导入从不携带凭据，因此原本需要凭据的连接会保持停用。
class DataSourceConfigurationImport(ApiModel):
    formatVersion: Literal[1] = Field(description="配置包格式版本")
    exportedAt: str = Field(default="", max_length=80)
    items: list[DataSourceConfigurationItem] = Field(min_length=1, max_length=100)
    conflictMode: Literal["skip", "rename", "update"] = "skip"
    dryRun: bool = True


# 结果列或元数据字段的对外描述；dataType 可能是数据库类型或查询结果推断类型。
class ColumnView(BaseModel):
    columnName: str = Field(description="真实字段名")
    dataType: str = Field(description="数据库类型或查询结果类型")
    comment: str = Field(default="", description="字段注释")
    nullable: bool = Field(default=True, description="是否允许空值")
    primaryKey: bool = Field(default=False, description="是否属于主键")
    businessKey: bool = Field(default=False, description="低代码元数据明确声明的业务主键")
    nameField: bool = Field(default=False, description="低代码元数据明确声明的名称字段")
    semanticType: str = Field(default="", description="真实字段语义类型，例如 Option")
    enumValues: list[dict] = Field(default_factory=list, description="同步所得代码与名称，不猜测缺失编码")
    enumSource: dict = Field(default_factory=dict, description="枚举所属实体与字段")
    enumComplete: bool = Field(default=False, description="该字段选项字典是否完整且无冲突")


# 表/视图/索引的元数据视图；rowCount 为 null 表示“未统计”，不能当作 0。
class TableView(BaseModel):
    tableName: str = Field(description="表、视图或索引的真实名称")
    tableComment: str = Field(default="", description="数据库表说明")
    tableType: str = Field(default="TABLE", description="TABLE/VIEW/INDEX")
    rowCount: int | None = Field(default=None, description="实际查询得到的行数；未统计时为 null")
    rowCountEstimated: bool = Field(default=False, description="行数是否来自数据库统计估算")
    dataSizeBytes: int | None = Field(default=None, description="数据库统计中的数据与索引大小；未知为 null")
    createTime: str | None = Field(default=None, description="数据库记录的建表时间；未知为 null")
    updateTime: str | None = Field(default=None, description="数据库记录的最近更新时间；未知为 null")
    columns: list[ColumnView] = Field(description="当前数据源配置允许访问的字段")
    foreignKeys: list[dict] = Field(default_factory=list, description="数据库中已定义的外键关系；不推断同名字段关系")
    declaredRelations: list[dict] = Field(default_factory=list, description="已同步的低代码关系声明；只含当前授权表字段")


# 分析查询入参：手工 SQL 或自然语言问题共用同一契约。
# maxRows 只是请求上限，实际执行时还要与数据源配置上限取小（见 queries.py）；
# parameters 以数据库绑定变量传递，不能用来拼接 SQL。
class QueryInput(ApiModel):
    datasource_id: str = Field(alias="datasourceId", min_length=1, max_length=26, description="已授权且启用的数据源")
    query: str = Field(min_length=1, max_length=30000, description="自然语言问题或只读 SQL")
    mode: Literal["sql", "natural_language"] = Field(default="natural_language", description="手工 SQL 或自然语言分析")
    session_id: str = Field(default="", alias="sessionId", max_length=120, description="当前用户的多轮分析会话标识")
    need_analysis: bool = Field(default=True, alias="needAnalysis", description="是否生成数据统计与解释")
    need_chart: bool = Field(default=True, alias="needChart", description="是否返回基于真实字段的图表建议")
    max_rows: int = Field(default=100, alias="maxRows", ge=1, le=10000, description="请求行数上限，不能超过数据源上限")
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict, description="命名 SQL 参数；以数据库绑定变量传递，不拼接字符串")

    @field_validator("query")
    @classmethod
    def require_query_content(cls, value: str) -> str:
        """拒绝纯空白查询，避免把空问题送进模型或 SQL 授权管线。"""
        if not value.strip():
            raise ValueError("请输入查询内容")
        return value


# 分析结果契约：data/totalCount 只覆盖本次返回样本，truncated 明确是否还有未返回行；
# Decimal 和超过 JavaScript 安全整数范围的值用十进制字符串传输，避免 JSON 往返失真。
class QueryResult(BaseModel):
    queryId: str = Field(description="可追溯的查询历史标识")
    runId: str | None = Field(default=None, description="统一运行中心标识，不依赖聊天会话")
    generatedSql: str = Field(description="经过语法、表字段和只读校验后实际执行的 SQL")
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict, description="本次查询的绑定参数，便于在授权范围内复用")
    queryType: str = Field(description="sql 或 natural_language")
    executionTime: int = Field(description="执行耗时毫秒")
    data: list[dict[str, Any]] = Field(description="限量真实查询结果；Decimal 和超出安全整数范围的值使用十进制字符串")
    totalCount: int = Field(description="本次实际返回行数，不将限量样本数冒充整库总数")
    truncated: bool = Field(description="是否还有未返回的行")
    columns: list[ColumnView] = Field(description="结果列、类型及注释")
    statistics: dict[str, Any] = Field(default_factory=dict, description="明确标记样本范围的确定性统计")
    enhancedAnalysis: dict[str, Any] = Field(default_factory=dict, description="仅基于返回结果的趋势、IQR 异常、谨慎投影与行动建议")
    chartRecommendations: list[dict[str, Any]] = Field(default_factory=list, description="基于返回字段和数值推荐的图表")
    analysis: str = Field(default="", description="统计口径、样本范围及可选模型解释")
    insights: list[dict[str, Any]] = Field(default_factory=list, description="可追溯到本次结果的质量或数值发现")
    retrievalEvidence: dict[str, Any] = Field(default_factory=dict, description="自然语言查询的意图、扩展词、业务域、召回通道与选表依据")
    success: bool = Field(default=True, description="查询是否完成")
    errorMessage: str = Field(default="", description="不含密码、连接串或服务器堆栈的错误")
    datasourceId: str = Field(description="数据来源标识")
    datasourceVersion: int = Field(description="本次使用的连接配置版本")
    modelUsage: dict[str, Any] = Field(default_factory=dict, description="模型名与使用量；手工 SQL 不调用模型")

    @field_validator("parameters", "data", "statistics", "enhancedAnalysis", "chartRecommendations", "insights",
                     "retrievalEvidence", "modelUsage", mode="before")
    @classmethod
    def preserve_saved_integer_precision(cls, value):
        """历史整数也遵守当前传输口径，不改写原始持久快照或重新执行查询。"""
        from app.common.json_numbers import preserve_json_integers
        return preserve_json_integers(value)


class InventoryQueryInput(ApiModel):
    datasource_id: str = Field(alias="datasourceId", min_length=1, max_length=26, description="已授权的库存业务数据源")
    kind: Literal["inventory", "warehouses", "materials"] = Field(default="inventory", description="库存批次明细、仓库目录或备件物料目录")
    warehouse_id: str = Field(default="", alias="warehouseId", max_length=160, description="明确指定的仓库 ID；不会改动工作流绑定")
    material_id: str = Field(default="", alias="materialId", max_length=160, description="明确指定的备件物料 ID")
    name: str = Field(default="", max_length=200, description="名称包含的字面文本；库存按备件名称，目录按当前对象名称")
    batch_number: str = Field(default="", alias="batchNumber", max_length=200, description="精确匹配库存批次号；仅库存查询可用")
    offset: int = Field(default=0, ge=0, le=100000, description="按稳定记录顺序分页的起始偏移")
    page_size: int = Field(default=100, alias="pageSize", ge=1, le=1000, description="本次明细上限；仍受数据源行数策略约束")

    @model_validator(mode="after")
    def validate_filters(self):
        """不同业务目录不能静默丢弃调用者提交的筛选条件。"""
        if self.kind == "warehouses" and (self.material_id or self.batch_number):
            raise ValueError("仓库目录不接受物料或批次筛选，请使用库存查询")
        if self.kind == "materials" and (self.warehouse_id or self.batch_number):
            raise ValueError("物料目录不接受仓库或批次筛选，请使用库存查询")
        return self


# 数据质量检查入参：tableName 必须来自元数据目录，columns 为空表示检查全部可访问字段。
class QualityInput(ApiModel):
    datasource_id: str = Field(alias="datasourceId", description="数据源标识")
    table_name: str = Field(alias="tableName", min_length=1, max_length=200, description="从元数据选择的表或索引")
    max_rows: int = Field(default=1000, alias="maxRows", ge=1, le=10000, description="质量检查抽样行数")
    columns: list[str] = Field(default_factory=list, description="检查字段；为空时检查全部可访问字段")


# 查询模板入参：保存模板不等于执行 SQL；复用（use）时才会单独校验 analytics.execute。
class QueryTemplateInput(ApiModel):
    name: str = Field(min_length=1, max_length=160, description="查询模板名")
    category: str = Field(default="未分类", max_length=100, description="查询分类")
    content: str = Field(min_length=1, max_length=30000, description="自然语言提示词或参数化 SQL")
    mode: Literal["sql", "natural_language"] = Field(default="natural_language", description="复用时的查询模式")
    parameters: dict[str, Any] = Field(default_factory=dict, description="参数说明、类型及非敏感示例")
    version: int | None = Field(default=None, ge=1, description="编辑所需的当前版本")


# 查询模板的对外视图；version 用于乐观锁，usageCount 是接口复用次数。
class QueryTemplateView(BaseModel):
    id: str = Field(description="查询模板标识")
    name: str = Field(description="查询模板名")
    category: str = Field(description="分类")
    content: str = Field(description="模板内容")
    mode: str = Field(description="sql/natural_language")
    parameters: dict = Field(description="参数定义")
    usageCount: int = Field(description="使用次数")
    version: int = Field(description="当前版本")


# 查询历史条目：只回传可追溯摘要（SQL、状态、耗时、行数），不含完整结果集。
class HistoryView(BaseModel):
    id: str = Field(description="历史查询标识")
    datasourceId: str = Field(description="数据源")
    queryText: str = Field(description="原始问题或 SQL")
    generatedSql: str = Field(description="执行的 SQL")
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict, description="成功查询保存的绑定参数")
    sessionId: str = Field(description="会话标识")
    queryMode: str = Field(description="查询模式")
    status: str = Field(description="执行状态")
    executionTime: int = Field(description="耗时毫秒")
    resultCount: int = Field(description="实际返回行数")
    errorMessage: str = Field(description="脱敏错误")
    createTime: str = Field(description="创建时间 UTC")

    @field_validator("parameters", mode="before")
    @classmethod
    def preserve_parameter_precision(cls, value):
        """历史参数可由浏览器复用，不能在读取时损失大整数尾数。"""
        from app.common.json_numbers import preserve_json_integers
        return preserve_json_integers(value)


# 导出入参：queryId 必须是已完成且当前用户仍可访问的查询；导出文件由平台生成下载，
# 不接受任意服务器输出路径。
class ExportInput(ApiModel):
    query_id: str = Field(alias="queryId", description="已完成且当前用户可访问的查询标识")
    format: Literal["csv", "xlsx", "json", "html", "markdown"] = Field(default="xlsx", description="导出格式；文件由平台提供下载，不接受任意服务器路径")
