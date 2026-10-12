"""Documented graph, operation, revision and run contracts for business workflows.

中文补充：工作流的全部对外契约（请求/响应模型 + 每个节点的配置模型）。
``catalog.CONFIGS`` 直接引用本文件的配置模型，因此这里同时是
"前端属性面板 schema""保存时的校验规则""执行时读取的字段"三者的唯一来源。

阅读要点：
- 每个字段的 ``description`` 会出现在节点目录接口与 AI 生成提示词里，属于用户可见文案，
  修改时需与 ``EDITABLE_CONFIG_DEFAULTS`` 保持一致。
- 别名（alias）统一是前端 camelCase；``by_alias=True`` 序列化是接口约定。
- 校验器只做"单节点内可判定"的检查（唯一性、二选一、取值范围、模板安全）；
  需要跨节点/跨资源的能力（环、可达性、资源授权）在 ``graph`` 模块完成。
- 图与配置的整体规模上限（节点 100、连线 300、表达式 24 层等）写在各字段约束里，
  它们是拒绝服务防护的一部分，不应放宽。
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.common.contracts import ApiModel
from app.modules.collection.schemas import ExtractField


_TEMPLATE_TOKEN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_UNSAFE_NAMES = {"__proto__", "constructor", "prototype"}


def safe_template(value: str) -> str:
    """Validate every runtime placeholder while the workflow is still a draft.

    中文补充：模板（HTTP URL、提示词、查询模板、文本模板）在**保存时**就校验占位符：
    最多 128 项、括号必须成对（不允许出现落单的 ``{{``/``}}``）、
    每个占位符只能是 ``input`` 或以 ``$`` 开头的可编译 JSONPath。
    这样运行期不会出现"模板里悄悄带了非法表达式"的情况；错误信息面向用户（可直接提示修改）。
    """
    matches = list(_TEMPLATE_TOKEN.finditer(value))
    if len(matches) > 128:
        raise ValueError("模板占位符最多 128 项")
    remainder = _TEMPLATE_TOKEN.sub("", value)
    if "{{" in remainder or "}}" in remainder:
        raise ValueError("模板占位符括号不完整")
    from app.modules.collection.extraction import compile_path

    for match in matches:
        token = match.group(1).strip()
        if token == "input":
            continue
        if not token.startswith("$"):
            raise ValueError("模板占位符只支持 input 或以 $ 开头的 JSONPath")
        compile_path(token)
    return value


def validate_named_fields(values: list[str], label: str, *, reserved: set[str] | None = None) -> list[str]:
    """Prevent blank, duplicate and prototype-like names from overwriting output.

    中文补充：字段/变量名的通用校验：非空、去空格后非空、不超过 120 字符、不重复、
    且不在保留集里。保留集默认包含 ``__proto__``/``constructor``/``prototype``
    （防止把前端对象原型键写进输出造成污染），调用方可追加自己的保留名
    （例如循环更新字段要保留 ``_index``/``_iteration``）。
    ``label`` 只用于错误文案。返回原列表；异常：ValueError（由 Pydantic 转成 422）。
    """
    reserved = _UNSAFE_NAMES | (reserved or set())
    if any(not isinstance(value, str) or not value.strip() or len(value) > 120 or value in reserved for value in values):
        raise ValueError(f"{label}必须非空、安全且不超过 120 字符")
    if len(set(values)) != len(values):
        raise ValueError(f"{label}不能重复")
    return values


class NodeReference(ApiModel):
    """命名输入到上游输出的引用：``nodeId`` 指定上游节点，``path`` 从中选单个值。

    ``nodeId`` 必须存在依赖连线（``graph.inspect_graph`` 用祖先集合校验），
    ``path`` 是 JSONPath，要求恰好匹配 1 个值——需要数组时请引用数组字段本身。
    """

    node_id: str = Field(alias="nodeId", min_length=1, max_length=80, description="上游节点标识；必须存在依赖连线")
    path: str = Field(default="$", max_length=500, description="从该节点输出选择单个值的 JSONPath；数组请引用数组本身")


class Position(ApiModel):
    """画布坐标，仅用于可视化；越界或非有限值会被拒绝（-20000 ~ 20000）。"""

    x: float = Field(default=0, ge=-20000, le=20000, allow_inf_nan=False, description="画布横坐标")
    y: float = Field(default=0, ge=-20000, le=20000, allow_inf_nan=False, description="画布纵坐标")


class WorkflowRetryConfig(ApiModel):
    """Dify-compatible retry controls persisted with each executable node."""

    retry_enabled: bool = Field(default=False, alias="retryEnabled", description="节点失败后是否自动重试")
    max_retries: int = Field(default=3, alias="maxRetries", ge=1, le=10, description="失败后的最大重试次数")
    retry_interval: int = Field(default=1000, alias="retryInterval", ge=100, le=5000, description="两次尝试之间的毫秒数")


class StartInputVariable(ApiModel):
    """One field rendered by both the workflow test runner and published Web app."""

    variable: str = Field(
        min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_]*$",
        description="开始节点输出中的变量名",
    )
    label: str = Field(min_length=1, max_length=120, description="运行表单中显示的字段名称")
    type: Literal[
        "text-input", "paragraph", "select", "number", "url", "json", "json_object",
        "checkbox", "file", "file-list",
    ] = Field(default="text-input", description="Dify 输入字段类型")
    required: bool = Field(default=False, description="运行前是否必须填写")
    max_length: int | None = Field(default=None, alias="maxLength", ge=1, le=100000, description="文本最大字符数")
    default: Any = Field(default=None, description="用户未填写时使用的默认值")
    hint: str = Field(default="", max_length=500, description="字段下方的帮助说明")
    placeholder: str = Field(default="", max_length=200, description="输入框占位提示")
    unit: str = Field(default="", max_length=30, description="数值字段单位")
    options: list[str] = Field(default_factory=list, max_length=100, description="选择字段的候选项")

    @model_validator(mode="after")
    def validate_field_options(self):
        """校验选择项与字段类型的搭配：只有 select 能配候选项，且默认值必须在候选项内。

        number/checkbox/file/file-list 不支持 maxLength（无文本长度概念），配置了即报错。
        """
        if self.type == "select":
            self.options = validate_named_fields(self.options, "选择项")
            if self.default is not None and self.default not in self.options:
                raise ValueError("选择字段默认值必须属于候选项")
        elif self.options:
            raise ValueError("只有选择字段可以配置候选项")
        if self.type in {"number", "checkbox", "file", "file-list"} and self.max_length is not None:
            raise ValueError("当前字段类型不支持最大长度")
        return self


class FlowNode(ApiModel):
    """图中的一个节点：类型 + 画布位置 + 输入映射 + 配置 + 重试/异常策略。

    关键约束：
    - ``kind`` 必须属于节点目录（``catalog.NODES``），未知类型不会被当成透传；
    - ``config`` 不接受未声明字段，实际校验由 ``graph.check_config`` 按 kind 执行
      （这里只是原始 dict，避免图模型与节点模型互相依赖）；
    - ``inputs`` 为空时表示"使用唯一有效上游"，多上游必须显式配置或使用 merge 节点；
    - 重试与异常策略只对部分 kind 生效（见 ``graph._RETRY_UNSUPPORTED`` 等名单），
      并且重试是否真的执行还要看 ``nodes.node_retry_is_safe``。
    """

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", description="图内唯一节点标识")
    kind: str = Field(min_length=1, max_length=60, description="节点目录中的类型，不把未知节点当成透传")
    title: str = Field(min_length=1, max_length=160, description="用户可读的节点名称")
    description: str = Field(default="", max_length=1000, description="节点用途说明，对应 Dify 节点描述")
    position: Position = Field(default_factory=Position, description="可视化画布位置")
    inputs: dict[str, NodeReference] = Field(default_factory=dict, description="命名输入到上游输出的映射；为空使用唯一有效上游")
    config: dict[str, Any] = Field(default_factory=dict, description="对应节点目录中的配置 schema，不接受未声明字段")
    retry_config: WorkflowRetryConfig = Field(default_factory=WorkflowRetryConfig, alias="retryConfig", description="失败重试策略")
    error_strategy: Literal["none", "default-value", "fail-branch"] = Field(
        default="none", alias="errorStrategy", description="停止流程、使用默认值或进入失败分支",
    )
    default_value: Any = Field(default=None, alias="defaultValue", description="异常处理选择默认值时的节点输出")


class FlowEdge(ApiModel):
    """一条连线。``port`` 决定分支语义，取值由正则限定：
    ``out``（普通输出）、``true``/``false``（condition）、``error``（异常分支）、
    ``case:<分类编码>``（classifier）。哪类节点能用哪个端口由 ``graph.inspect_graph`` 校验，
    这里只保证字面量合法。``sourceSide``/``targetSide`` 只影响画布几何，不改变执行顺序。
    """

    id: str = Field(min_length=1, max_length=100, description="图内唯一连线标识")
    source: str = Field(min_length=1, max_length=80, description="来源节点")
    target: str = Field(min_length=1, max_length=80, description="目标节点")
    port: str = Field(default="out", max_length=85, pattern=r"^(out|true|false|error|case:[A-Za-z][A-Za-z0-9_-]{0,79})$", description="普通输出、条件/异常分支或 case:分类编码")
    source_side: Literal["left", "right", "top", "bottom"] = Field(
        default="right", alias="sourceSide", description="连线离开来源节点的画布方向；只影响可视化几何，不改变分支语义",
    )
    target_side: Literal["left", "right", "top", "bottom"] = Field(
        default="left", alias="targetSide", description="连线进入目标节点的画布方向；只影响可视化几何，不改变执行顺序",
    )


class FlowGraph(ApiModel):
    """完整流程图（节点 + 连线）。节点上限 100、连线上限 300，是执行与校验的输入单位；
    容器节点的子图也是同一个类型，因此校验逻辑可以递归复用。
    """

    nodes: list[FlowNode] = Field(default_factory=list, max_length=100, description="完整节点列表，最多 100 个")
    edges: list[FlowEdge] = Field(default_factory=list, max_length=300, description="完整连线列表，最多 300 条")


class WorkflowInput(ApiModel):
    """创建/更新草稿的请求体。``version`` 是乐观锁：更新时必须与当前版本一致，否则 409。
    名称做非空去空格处理；业务域至少一个、去重后保存。
    """

    name: str = Field(min_length=1, max_length=200, description="工作流名称")
    description: str = Field(default="", max_length=2000, description="业务用途与团队说明")
    domains: list[Literal["general", "analysis", "alert", "quality"]] = Field(default_factory=lambda: ["general"], min_length=1, max_length=4, description="可复用的业务域，可同时用于设备预警和质检")
    graph: FlowGraph = Field(description="完整流程图；草稿允许未连接，但不允许未知节点或无效引用")
    version: int | None = Field(default=None, ge=1, description="编辑时加载到的乐观锁版本，过期返回 409")

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        """名称非空并去除首尾空格；全空白视为未填写。"""
        if not value.strip():
            raise ValueError("请输入工作流名称")
        return value.strip()


class VersionInput(ApiModel):
    """带乐观锁版本的最小请求体（发布/停用用）。版本过期返回 409。"""

    version: int = Field(ge=1, description="加载时的当前记录版本")


class RestoreInput(VersionInput):
    """恢复历史修订的请求体：``version`` 是当前草稿版本，``revision`` 是要复制的历史修订号。

    恢复只生成新草稿，不改写历史修订，也不改变当前发布版本。
    """

    revision: int = Field(ge=1, description="要复制为新草稿的历史修订号，不覆盖历史")


class RunInput(VersionInput):
    """执行请求体。

    ``mode`` 决定运行哪个版本：published 用发布修订，draft 需要编辑权限做试运行。
    ``idempotencyKey`` 是本次执行的唯一键（至少 8 位、仅字母数字下划线连字符）：
    相同键 + 相同流程/版本/输入返回原执行，不会重复发送 HTTP 或重复写库。
    ``inputs`` 是开始节点的输入对象（最大 1 MB，不自动补演示数据）。
    图中含 output-table 时必须显式 ``confirmWrites=true``。
    ``timeoutSeconds`` 限制整条流程的执行时长，到期不再开始后续节点。
    """

    confirm_writes: bool = Field(default=False, alias="confirmWrites", strict=True, description="明确确认执行所选版本的外部表写入；含写入节点时必须为 true，查看/生成报表不自动授权")
    inputs: dict[str, Any] = Field(default_factory=dict, description="开始节点输入，JSON 对象，最大 1 MB；不会自动补入演示数据")
    mode: Literal["draft", "published"] = Field(default="published", description="已发布版本运行，或需编辑权限的草稿试运行")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$", description="本次执行的唯一键；相同键与输入返回原运行，不重新发送 HTTP")
    timeout_seconds: int = Field(default=120, alias="timeoutSeconds", ge=1, le=300, description="整条流程最多执行秒数；到期不开始后续节点")


class ExecutionControlInput(ApiModel):
    """Optimistically control one persisted workflow execution."""

    version: int = Field(ge=1, description="执行记录当前版本；过期控制请求返回 409")
    idempotency_key: str = Field(
        alias="idempotencyKey", min_length=8, max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="暂停恢复或取消操作的唯一键；重复提交不重复推进节点",
    )
    input: Any = Field(default=None, description="提问节点的补充输入；延迟节点恢复时可为空")


class WorkflowGenerateInput(ApiModel):
    """AI 生成草稿的请求体。

    ``resourceIds`` 是用户**明确勾选**的数据源：只把这些资源的目录交给模型；
    留空时模型不会猜测或默认绑定任何数据源（生成的节点 datasourceId 为空）。
    ``prompt`` 至少要 8 个字符，业务目标描述越具体生成结果越可用。
    """

    prompt: str = Field(min_length=8, max_length=8000, description="希望生成的流程目标、输入、规则和输出")
    domains: list[Literal["general", "analysis", "alert", "quality"]] = Field(
        default_factory=lambda: ["general"], min_length=1, max_length=4,
        description="生成流程的业务域",
    )
    name: str = Field(default="AI 生成工作流", min_length=1, max_length=200, description="草稿名称")
    resource_ids: list[str] = Field(
        default_factory=list, alias="resourceIds", max_length=100,
        description="用户在生成界面明确选择的授权数据源；不选择时 AI 不会猜测或默认绑定第一个数据源",
    )

    @field_validator("resource_ids")
    @classmethod
    def unique_resource_ids(cls, values: list[str]) -> list[str]:
        """数据源标识必须非空且不超过 id 长度上限，并按首次出现顺序去重。"""
        if any(not value or len(value) > 26 for value in values):
            raise ValueError("数据源标识无效")
        return list(dict.fromkeys(values))


class WorkflowView(BaseModel):
    """流程视图：草稿图 + 乐观锁版本 + 发布状态 + **实时**校验结果。

    ``validation`` 每次读取都会重算，草稿带着错误也能保存，但用户能立刻看到每一项阻塞原因。
    ``publishedRevision`` 是正式运行使用的不可变修订号，编辑草稿不会改变它。
    """

    id: str = Field(description="工作流标识")
    name: str = Field(description="工作流名称")
    description: str = Field(description="业务说明")
    domains: list[str] = Field(description="共享业务域")
    graph: FlowGraph = Field(description="最新可编辑图")
    version: int = Field(description="当前乐观锁版本")
    publishedRevision: int | None = Field(description="当前正式运行的不可变修订号，未发布为空")
    status: str = Field(description="draft/published/disabled")
    validation: dict = Field(description="当前图的发布/执行就绪检查；草稿错误可保留并逐项修复")
    createTime: str = Field(description="创建时间 UTC")
    updateTime: str = Field(description="更新时间 UTC")


class DeviceWorkflowEntryInput(ApiModel):
    """从设备页进入其业务工作流的请求体（设备标识 + 业务域 alert/quality）。"""

    device_id: str = Field(alias="deviceId", min_length=1, max_length=26, description="用户选中的共享设备标识")
    domain: Literal["alert", "quality"] = Field(description="从设备预警或质量检查进入")


class DeviceWorkflowEntryView(BaseModel):
    """设备入口视图：绑定标识、设备摘要、承载的编辑器提供方与可打开的站内路径。

    ``provider`` 为 dify 时 ``providerAppId`` 指向外部应用；平台编辑器时为空。
    ``created`` 表示本次是否首次创建默认草稿（前端据此引导用户配置）。
    """

    bindingId: str = Field(description="设备与业务工作流的稳定绑定标识")
    device: dict[str, Any] = Field(description="当前有权读取的设备摘要")
    domain: Literal["alert", "quality"] = Field(description="当前业务入口")
    provider: Literal["platform", "dify"] = Field(description="实际承载工作流编辑器的提供方")
    providerAppId: str | None = Field(description="外部编辑器应用标识；平台编辑器为空")
    workflow: WorkflowView = Field(description="当前可编辑的工作流草稿或已发布版本")
    editorPath: str = Field(description="站内可直接打开的设备工作流地址")
    created: bool = Field(description="本次操作是否首次创建默认草稿")


class EmptyConfig(ApiModel):
    """Start/end/merge nodes have no implicit additional configuration."""


class StartConfig(ApiModel):
    """开始节点配置：运行输入字段定义，以及"按时间增量采集"窗口设置。

    开启 ``incrementalWindow`` 后，数据库查询节点可以绑定 windowStart/windowEnd/afterKey，
    由 ``windows`` 模块维护水位线；``lookbackMinutes`` 只影响首次自动运行的回看范围。
    变量名必须唯一且是合法标识符（前端运行表单与发布后的 Web 应用共用这份定义）。
    """

    incremental_window: bool = Field(default=False, alias="incrementalWindow", description="按上次成功采集至本次运行的时间收集新增与更新数据")
    lookback_minutes: int = Field(default=30, alias="lookbackMinutes", ge=1, le=5256000, description="首次自动运行的回看分钟数")
    time_zone: Literal["Asia/Shanghai", "UTC"] = Field(default="Asia/Shanghai", alias="timeZone")
    variables: list[StartInputVariable] = Field(default_factory=list, max_length=50, description="运行前由用户填写的输入字段")

    @field_validator("variables")
    @classmethod
    def unique_variables(cls, values: list[StartInputVariable]) -> list[StartInputVariable]:
        """开始节点变量名必须唯一且安全（运行表单与发布后的 Web 应用按名字取值）。"""
        validate_named_fields([item.variable for item in values], "输入变量")
        return values


class HttpAuthConfig(ApiModel):
    """Reference credentials from the bound HTTP source without storing secrets in a graph."""

    type: Literal["none", "bearer", "basic", "api-key"] = Field(default="none", description="请求认证方式")
    credential_name: str | None = Field(
        default=None, alias="credentialName", min_length=1, max_length=60,
        pattern=r"^[A-Za-z][A-Za-z0-9_]{0,59}$", description="绑定 HTTP 数据源中的凭据名称",
    )
    header_name: str = Field(
        default="Authorization", alias="headerName", min_length=1, max_length=100,
        pattern=r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$", description="api-key 写入的请求头名称",
    )

    @model_validator(mode="after")
    def valid_credential_reference(self):
        """认证方式与凭据名称必须成对出现，且头部使用受限制。

        规则：未选认证方式不能填凭据名；选了认证方式必须填；Bearer/Basic 只能写 Authorization 头；
        api-key 的头名用占位凭据走一次 ``validate_headers``，提前拦掉非法头名与敏感头。
        """
        if self.type == "none" and self.credential_name:
            raise ValueError("未选择认证方式时不能填写凭据名称")
        if self.type != "none" and not self.credential_name:
            raise ValueError("认证方式需要选择绑定数据源中的凭据名称")
        if self.type in {"bearer", "basic"} and self.header_name.lower() != "authorization":
            raise ValueError("Bearer/Basic 认证只能写入 Authorization 请求头")
        if self.type == "api-key":
            from app.modules.collection.transport import validate_headers
            validate_headers({self.header_name: "${secret.placeholder}"})
        return self


class DataSourceNodeConfig(ApiModel):
    """Common binding contract shared by HTTP and database nodes."""

    datasource_id: str = Field(alias="datasourceId", min_length=1, max_length=26, description="已授权的数据源")
    datasource_version: int | None = Field(
        default=None, alias="datasourceVersion", ge=1,
        description="可选固定数据源版本；业务向导创建的正式流程必须填写",
    )


class HttpConfig(DataSourceNodeConfig):
    """A request override that stays bound to an authorized HTTP data source."""

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] | None = Field(
        default=None, description="请求方法；省略时沿用已绑定数据源，写方法需显式确认",
    )
    url: str | None = Field(
        default=None, min_length=1, max_length=2000, description="固定或安全模板 URL；省略时沿用数据源地址，不允许凭据或跳转",
    )
    headers: dict[str, str] = Field(default_factory=dict, description="请求头覆盖；认证值只能引用绑定数据源凭据")
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict, description="查询参数；运行时模板只来自已映射输入")
    body: Any = Field(default=None, description="JSON 请求体；敏感字段只能引用凭据，GET 会拒绝非空请求体")
    auth: HttpAuthConfig = Field(default_factory=HttpAuthConfig, description="绑定数据源凭据的 Bearer、Basic 或 API Key 认证")
    response_format: Literal["json", "text"] | None = Field(default=None, alias="responseFormat", description="解析 JSON 或文本响应；省略时沿用数据源")
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds", ge=1, le=60, description="单条请求超时；省略时沿用数据源设置")
    retry: int = Field(default=0, ge=0, le=5, description="网络/上游失败后的额外请求次数；验证错误不会重试")
    retry_interval: int = Field(default=500, alias="retryInterval", ge=100, le=5000, description="请求重试间隔毫秒")
    confirm_writes: bool = Field(default=False, alias="confirmWrites", description="确认 POST/PUT/PATCH/DELETE 可能产生外部副作用")

    @field_validator("url")
    @classmethod
    def valid_url_template(cls, value: str | None) -> str | None:
        """URL 模板保存时先校验静态形态：占位符合法，且把占位符替换成示例后能通过传输层校验。

        运行期渲染出真实地址后会再校验一次（含 DNS 与私网判断），这里只做早失败。
        """
        if value is None:
            return value
        safe_template(value)
        # Validate the static shape now; runtime values are validated again by
        # the pinned transport after rendering, including DNS and private range.
        from app.modules.collection.transport import validate_url
        probe = re.sub(r"\{\{\s*[^{}]+?\s*\}\}", "example", value)
        validate_url(probe)
        return value

    @field_validator("headers")
    @classmethod
    def valid_request_headers(cls, value: dict[str, str]) -> dict[str, str]:
        """请求头名称与取值走传输层统一校验（拒绝被禁头名与明文凭据）。"""
        from app.modules.collection.transport import validate_headers
        validate_headers(value)
        return value

    @field_validator("body")
    @classmethod
    def valid_request_body(cls, value: Any) -> Any:
        """请求体只允许以 ``${secret.xxx}`` 引用凭据，不得出现明文密钥。"""
        from app.modules.collection.transport import validate_body_secrets
        validate_body_secrets(value)
        return value

    @field_validator("params")
    @classmethod
    def valid_query_params(cls, value: dict[str, str | int | float | bool | None]):
        """查询参数名必须符合标识符格式，且认证类参数不能放进 URL（会写进日志与追溯）。

        需要认证时请使用绑定数据源的凭据配置，而不是查询参数。
        """
        from app.modules.collection.transport import sensitive_name
        for key, item in value.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", key):
                raise ValueError("查询参数名称格式不正确")
            if sensitive_name(key) and item not in (None, ""):
                raise ValueError("认证参数不能放入 URL 查询字符串，请使用绑定凭据")
        return value


class QueryConfig(DataSourceNodeConfig):
    """数据库查询 / AI 数据分析节点的配置。

    这是**需要手写 SQL 的节点**（与"读取值"选表模式相对）：``query`` 在 database-query 下是
    只读 SQL（由分析模块做只读校验），在 report-analysis 下是自然语言问题。
    增量与扫描两种游标互不相同：
    - ``windowKey`` 配 ``windows`` 采集窗口，SQL 里绑定 ``:windowStart/:windowEnd/:afterKey``，
      只有整轮成功才推进水位线；
    - ``scanKey`` 配有界连续扫描，SQL 必须使用 ``:afterKey`` 并按该列升序，扫到末尾后
      下一轮从首条重新复核已修改记录。
    ``parameters`` 可被 ``inputs.parameters`` 覆盖（上游提供运行时值）。
    """

    window_key: str | None = Field(default=None, alias="windowKey", max_length=120, description="按采集窗口分批取数的唯一整数列；SQL 绑定 windowStart、windowEnd、afterKey")
    query: str = Field(min_length=1, max_length=30000, description="只读 SQL 或 report-analysis 节点的自然语言问题")
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict, description="SQL 绑定参数；可由 inputs.parameters 提供运行时值")
    max_rows: int = Field(default=1000, alias="maxRows", ge=1, le=10000, description="最大查询行数")
    scan_key: str | None = Field(default=None, alias="scanKey", max_length=120, description="有界连续扫描的单调整数列；SQL 使用 :afterKey 并升序排列。整次流程成功后保存位置，扫描结束后重新复核已修改记录。")


class BusinessReportConfig(ApiModel):
    """业务报告节点配置：报告类型、标题与可选的 AI 重点解读。

    ``kind`` 决定字段成形规则（质检列与设备预警列不同，见 business_report.business_rows）；
    ``aiAnalysis`` 开启后会调用模型做"重点解读"，模型只能选择要关注的项目与建议动作，
    不能改变既有判定结论；模型不可用时只降级为"暂未完成"，不影响报告保存。
    """

    kind: Literal["quality", "alert"] = "alert"
    title: str = Field(default="业务检查报告", min_length=1, max_length=160)
    ai_analysis: bool = Field(default=True, alias="aiAnalysis")
    model_profile: str = Field(default="reasoning", alias="modelProfile", max_length=100)


class BusinessNoticeConfig(ApiModel):
    """业务报告通知配置：收件范围（executor 仅执行人 / all 工作区全部启用成员）。

    ``audience="all"`` 在执行与发布时会额外要求 ``system.operations.manage`` 权限
    （见 graph.authorize_graph），避免把业务数据广播给无权查看的人。
    """

    audience: Literal["executor", "all"] = "executor"


class DocumentExtractorConfig(ApiModel):
    """文档提取器配置：仅一个多文件文本之间的分隔符（解析走文件模块的预览能力）。"""

    separator: str = Field(default="\n\n", max_length=100, description="多个文件文本之间的分隔符")


class ListOperationConfig(ApiModel):
    """列表操作配置：可选的筛选、排序、取前 N 项。

    三项能力由 ``filterEnabled``/``sortEnabled``/``limitEnabled`` 分别开关；
    筛选表达式对基础类型项使用 ``item`` 字段（见 nodes.execute_node 的 list-operator 分支）。
    """

    filter_enabled: bool = Field(default=False, alias="filterEnabled")
    expression: dict = Field(default_factory=lambda: {"value": True}, description="列表项筛选条件；基础类型使用 item 字段")
    sort_enabled: bool = Field(default=False, alias="sortEnabled")
    sort_field: str = Field(default="", alias="sortField", max_length=120, description="对象排序字段；基础类型留空")
    sort_order: Literal["asc", "desc"] = Field(default="asc", alias="sortOrder")
    limit_enabled: bool = Field(default=False, alias="limitEnabled")
    limit: int = Field(default=10, ge=1, le=10000)


class ExtractionConfig(ApiModel):
    """字段提取（jsonpath-extract）配置：记录路径 + 输出字段定义。

    这是**需要手写 JSONPath 的节点**：``recordPath`` 从上游输出定位记录数组，
    每个字段自带 path 与类型（``ExtractField`` 来自采集模块，与 HTTP 采集共用同一套约定）。
    结果被截断时报错，不能继续整批判定。
    """

    record_path: str = Field(default="$.data", alias="recordPath", min_length=1, max_length=500, description="从上游输出选取记录的 JSONPath")
    fields: list[ExtractField] = Field(default_factory=list, max_length=200, description="需要的输出字段、类型、必填和多值约定")


class CalculatedField(ApiModel):
    """计算字段的一项输出：公式与表达式树**必须且只能**选一个。

    公式是给人看的写法（支持中文字段名，仅算术），表达式树是可视化编辑器的产物；
    两者最终都走 ``expressions`` 的同一套安全求值，输出字段名允许覆盖当前行同名字段。
    """

    name: str = Field(min_length=1, max_length=120, description="输出字段名，允许显式覆盖当前行同名字段")
    formula: str | None = Field(default=None, max_length=2000, description="加减乘除与括号公式；字段名支持中文，不执行代码")
    expression: dict | None = Field(default=None, description="可视化运算表达式树，与公式二选一")

    @model_validator(mode="after")
    def exactly_one_expression(self):
        """公式与表达式树必须二选一：同时给或都不给都无法确定求值口径。"""
        if (self.formula is None) == (self.expression is None):
            raise ValueError("公式与表达式树必须且只能选择一项")
        return self


class ArithmeticConfig(ApiModel):
    """计算字段节点配置：按顺序计算多个输出字段，后项可引用前项结果。"""

    fields: list[CalculatedField] = Field(min_length=1, max_length=100, description="依次计算的输出字段，后项可引用前项结果")


class PredicateConfig(ApiModel):
    """条件分支 / 记录筛选共用的布尔表达式配置。

    ``mode="record"`` 对整个对象判断并按 true/false 端口分流；
    ``mode="rows"`` 逐行判断（输出 rowBranches，供下游按端口取行数组）。
    筛选（filter）语义上等同于逐行保留 true 的行。
    """

    expression: dict = Field(description="返回布尔值的可视化表达式树；支持 AND/OR、比较、范围与缺失判断")
    mode: Literal["record", "rows"] = Field(default="record", description="判断一个对象，或将每行分别送入满足/不满足分支")


class SetConfig(ApiModel):
    """交并差配置。``keys`` 为空时比较完整记录，非空时按这些字段匹配；匹配命中时保留左侧记录。

    运算结果顺序稳定（首次出现者优先），便于逐行追溯。
    """

    operation: Literal["union", "intersection", "difference"] = Field(description="稳定顺序的并、交、左差")
    keys: list[str] = Field(default_factory=list, max_length=30, description="记录匹配字段；空列表比较完整值，匹配时保留左侧记录")

    @field_validator("keys")
    @classmethod
    def unique_keys(cls, values: list[str]) -> list[str]:
        """匹配字段去重并校验安全；空列表表示"比较完整记录"，保持原样返回。"""
        return validate_named_fields(values, "集合匹配字段") if values else values


class Rule(ApiModel):
    """一条业务规则项。``expression`` 的含义取决于模式：alert 模式是异常条件（命中即问题），
    quality 模式是合格条件（不命中即问题）。``message`` 只作为文本展示，不会执行其中的模板或代码。
    """

    id: str = Field(min_length=1, max_length=80, description="规则项标识，用于逐条追溯")
    name: str = Field(min_length=1, max_length=160, description="规则项名称")
    expression: dict = Field(description="布尔表达式；预警模式为异常条件，质检模式为合格条件")
    severity: Literal["info", "warning", "critical"] = Field(default="warning", description="问题等级")
    message: str = Field(default="", max_length=1000, description="问题说明，不执行其中的模板或代码")


class RulesConfig(ApiModel):
    """逐项规则判定配置：模式 + 至少一条规则。

    关键约定：字段缺失或类型错误属于**执行失败**，不会被静默判为合格——
    契约明确要求"不能把没读到数据当成合格"。
    """

    mode: Literal["alert", "quality"] = Field(default="alert", description="异常条件触发问题，或合格条件不满足时触发问题")
    rules: list[Rule] = Field(min_length=1, max_length=100, description="逐条业务判断；缺失/类型错误是执行失败，不误判为合格")


class AlertConfig(ApiModel):
    """设备预警业务节点配置：固定策略修订号 + 可选绑定设备。

    ``ruleRevision`` 是**固定**的预警策略修订，策略后续被编辑不会自动替换这里的版本；
    从设备入口创建的流程会固定 ``deviceId``，通用流程可留空。
    """

    rule_id: str = Field(alias="ruleId", min_length=1, max_length=26, description="设备预警模块中明确选择的策略标识")
    rule_revision: int = Field(alias="ruleRevision", ge=1, description="固定的预警策略修订；编辑策略不自动替换此版本")
    device_id: str | None = Field(
        default=None, alias="deviceId", min_length=1, max_length=26,
        description="从设备入口创建的工作流固定设备；通用工作流可不填写",
    )


class AlertNotificationConfig(ApiModel):
    """设备预警站内通知配置：触发条件（哪些判定结果要通知）、最低等级与收件范围。

    ``outcomes`` 去重后保存；``minimumSeverity`` 只用于筛掉低等级问题，不放宽判定口径。
    ``audience="all"`` 需要额外权限，见 ``BusinessNoticeConfig``。
    """

    audience: Literal["executor", "all"] = Field(
        default="executor", description="通知本次执行人或当前工作区全部启用成员",
    )
    outcomes: list[Literal["issues", "no_data", "normal"]] = Field(
        default_factory=lambda: ["issues", "no_data"], min_length=1, max_length=3,
        description="触发站内消息的预警报告判定",
    )
    minimum_severity: Literal["info", "warning", "critical"] = Field(
        default="info", alias="minimumSeverity", description="问题报告需要达到的最低等级",
    )

    @field_validator("outcomes")
    @classmethod
    def unique_notification_outcomes(cls, values: list[str]) -> list[str]:
        """触发通知的判定结果去重（重复配置没有意义，保留首次出现顺序）。"""
        return list(dict.fromkeys(values))


class PromptNodeConfig(ApiModel):
    """大模型类节点的共同配置基类（llm / extractor / classifier 继承）。

    ``system_prompt`` 是系统约束，``prompt_template`` 只能使用安全的 ``{{ input }}`` 或
    ``{{ $.path }}`` 占位符（保存即校验）；``temperature`` 默认 0.1 偏向确定性输出。
    ``model_profile`` 指向当前工作区启用的模型配置名称。
    """

    model_profile: str = Field(default="reasoning", alias="modelProfile", min_length=1, max_length=100, description="当前工作区启用的模型配置名称")
    system_prompt: str = Field(default="", alias="systemPrompt", max_length=12000, description="系统约束；不会自动添加未授权业务数据")
    prompt_template: str = Field(default="{{ input }}", alias="promptTemplate", min_length=1, max_length=20000, description="使用 {{ input }} 或 {{ $.path }} 引用节点输入")
    temperature: float = Field(default=0.1, ge=0, le=2, allow_inf_nan=False, description="模型采样温度")

    @field_validator("prompt_template")
    @classmethod
    def valid_prompt_template(cls, value: str) -> str:
        """提示词模板只允许安全占位符：不能靠模板把未授权数据带进/带出模型调用。"""
        return safe_template(value)


class LlmConfig(PromptNodeConfig):
    """A single traced model call without an autonomous tool loop."""


class ExtractionFieldSpec(ApiModel):
    """信息抽取的输出字段契约：名称、JSON 类型、含义说明、是否必填。

    这份契约会拼进系统提示词，同时用于严格校验模型输出（契约外字段与类型不符都算失败）。
    """

    name: str = Field(min_length=1, max_length=120, description="结构化输出字段名")
    type: Literal["string", "number", "integer", "boolean", "array", "object"] = Field(default="string", description="输出 JSON 类型")
    description: str = Field(default="", max_length=1000, description="字段含义与抽取口径")
    required: bool = Field(default=False, description="未抽取到该字段时是否失败")


class ExtractorConfig(PromptNodeConfig):
    """信息抽取节点配置：在提示词节点基础上增加字段契约，字段名不能重复。"""

    fields: list[ExtractionFieldSpec] = Field(min_length=1, max_length=100, description="模型必须返回的结构化字段")

    @model_validator(mode="after")
    def unique_fields(self):
        """抽取字段名不能重复：字段契约会拼进提示词并用于严格校验模型输出。"""
        if len({item.name for item in self.fields}) != len(self.fields):
            raise ValueError("抽取字段名不能重复")
        return self


class AgentConfig(ApiModel):
    """智能体节点配置：调用已配置的 Bot。

    ``usePublished=True`` 运行 Bot 的已发布不可变版本；关闭时属于草稿测试，需要编辑权限。
    ``mode`` 控制只检索证据还是基于证据调用模型。
    """

    bot_id: str = Field(alias="botId", min_length=1, max_length=26, description="已配置的 Bot/Agent 标识")
    mode: Literal["retrieval", "model"] = Field(default="model", description="仅检索或基于证据调用模型")
    use_published: bool = Field(default=True, alias="usePublished", description="运行已发布不可变版本；关闭时仅供有编辑权限的草稿测试")
    query_template: str = Field(default="{{ input }}", alias="queryTemplate", min_length=1, max_length=8000, description="发送给 Bot 的问题模板")

    @field_validator("query_template")
    @classmethod
    def valid_query_template(cls, value: str) -> str:
        """发给 Bot 的问题模板只允许安全占位符（同提示词模板约束）。"""
        return safe_template(value)


class KnowledgeConfig(ApiModel):
    """知识检索节点配置：限定已授权知识库、检索问题模板、返回条数与最低相关分。

    执行与发布时会逐个校验知识库属于当前工作区且可用（撤销权限即失效），
    因此这里只做形态校验，实际授权在 ``graph.authorize_graph``。
    """

    knowledge_base_ids: list[str] = Field(alias="knowledgeBaseIds", min_length=1, max_length=100, description="限定已授权知识库")
    query_template: str = Field(default="{{ input }}", alias="queryTemplate", min_length=1, max_length=8000, description="检索问题模板")
    top_k: int = Field(default=6, alias="topK", ge=1, le=20, description="最多返回证据片段")
    min_score: float = Field(default=0, alias="minScore", ge=0, allow_inf_nan=False, description="最低相关分数")
    mode: Literal["hybrid", "keyword", "semantic"] = Field(default="hybrid", description="混合、关键词或语义检索")

    @field_validator("query_template")
    @classmethod
    def valid_query_template(cls, value: str) -> str:
        """检索问题模板只允许安全占位符（同提示词模板约束）。"""
        return safe_template(value)


class QuestionField(ApiModel):
    """One typed field in a durable human-intervention request."""

    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$", description="恢复输入字段名")
    label: str = Field(min_length=1, max_length=200, description="操作人看到的字段标题")
    type: Literal["text-input", "paragraph", "number", "select", "checkbox", "json", "json_object"] = Field(default="text-input")
    required: bool = Field(default=False, description="是否必须填写")
    options: list[str] = Field(default_factory=list, max_length=100, description="select 字段候选项")
    placeholder: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def valid_options(self):
        """只有 select 提问字段能配置候选项，且候选项须非空、不重复、安全。"""
        if self.type == "select":
            self.options = validate_named_fields(self.options, "提问选择项")
        elif self.options:
            raise ValueError("只有 select 提问字段可以配置选项")
        return self


class QuestionConfig(ApiModel):
    """提问节点配置：持久化等待人工介入的问题、回答字段名与结构化字段定义。

    ``fields`` 为空时兼容"单文本回答"；非空时恢复输入必须是严格匹配这些字段的 JSON 对象，
    未声明字段会被拒绝。``required`` 只禁止空白回答，不限制内容长度（由字段类型决定）。
    """

    prompt: str = Field(min_length=1, max_length=4000, description="暂停时展示给操作人的问题")
    answer_key: str = Field(default="answer", alias="answerKey", min_length=1, max_length=120, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$", description="恢复后输出对象中的回答字段")
    required: bool = Field(default=True, description="是否禁止空回答")
    fields: list[QuestionField] = Field(default_factory=list, max_length=30, description="结构化人工介入字段；为空时兼容单文本回答")

    @model_validator(mode="after")
    def unique_fields(self):
        """结构化提问字段名必须唯一且安全（恢复接口按名字取答案）。"""
        validate_named_fields([item.name for item in self.fields], "提问字段")
        return self


class DelayConfig(ApiModel):
    """延迟节点配置：持久化等待秒数（最长七天）。

    等待是落库的：重启或长时间等待后仍可由恢复接口续跑，不是进程内 sleep。
    """

    seconds: int = Field(ge=1, le=604800, description="持久化等待秒数，最长七天")


class ClassifierCase(ApiModel):
    """问题分类器的一个分类：编码、名称、判定依据说明，以及规则模式下的匹配表达式。

    ``description`` 在模型模式下是给模型的分类依据（说明越清楚分类越准）；
    模型模式可以不带 ``expression``，规则模式必须带（见 ClassifierConfig 的校验）。
    """

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", description="分类编码")
    label: str = Field(min_length=1, max_length=160, description="分类名称")
    description: str = Field(default="", max_length=3000, description="此分类包含的问题与区分依据")
    expression: dict | None = Field(default=None, description="规则模式按顺序匹配的布尔表达式")


class ClassifierConfig(PromptNodeConfig):
    """问题分类器配置（规则模式或模型模式）。

    两种模式共用 ``cases``/``fallback``：规则模式按顺序匹配表达式，模型模式把分类编码与说明交给模型。
    约束：分类编码不能重复，``fallback`` 不能与已配置分类重名（否则"未匹配"与"命中"无法区分），
    规则模式必须为每个分类配置条件。
    """

    mode: Literal["rules", "model"] = Field(default="rules", description="旧配置默认规则模式；问题分类采用模型模式")
    cases: list[ClassifierCase] = Field(min_length=1, max_length=50, description="按顺序判定的分类规则")
    fallback: str = Field(default="other", min_length=1, max_length=80, description="全部未匹配时的分类编码")

    @model_validator(mode="after")
    def unique_cases(self):
        """分类编码唯一、fallback 不与分类重名、规则模式必须给出条件、名称不能空白。

        这些约束共同保证"命中分类"与"未匹配"在运行时可以严格区分（见 nodes._classify_question）。
        """
        if len({item.id for item in self.cases}) != len(self.cases):
            raise ValueError("分类编码不能重复")
        if self.fallback in {item.id for item in self.cases}:
            raise ValueError("未匹配分类编码不能与已配置分类重复")
        if self.mode == "rules" and any(item.expression is None for item in self.cases):
            raise ValueError("规则分类必须为每个分类配置判断条件")
        if any(not item.label.strip() for item in self.cases):
            raise ValueError("分类名称不能为空白")
        return self


class VariableAssignment(ApiModel):
    """设置变量的一项赋值：``path``（从节点输入取单值）与 ``value``（固定 JSON 值）二选一。

    校验依据是"字段是否被显式提供"（``model_fields_set``），因此 ``value`` 允许显式写 null，
    这与"没写 value"是两种不同配置——修改这里时要保持这个区分。
    """

    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff-]*$", description="输出变量名")
    path: str | None = Field(default=None, max_length=500, description="从整个节点输入读取单值的 JSONPath")
    value: Any = Field(default=None, description="固定 JSON 值；与 path 二选一")

    @model_validator(mode="after")
    def one_source(self):
        """path 与 value 必须且只能配置一项（依据"字段是否被显式提供"判断，允许显式写 null）。"""
        has_value = "value" in self.model_fields_set
        if (self.path is None) == (not has_value):
            raise ValueError("变量必须且只能配置 path 或 value")
        return self


class SetVariableConfig(ApiModel):
    """设置变量节点配置：显式变量映射，``keepInput`` 决定是否保留对象输入的原有字段。"""

    variables: list[VariableAssignment] = Field(min_length=1, max_length=100, description="显式变量映射")
    keep_input: bool = Field(default=True, alias="keepInput", description="是否将对象输入字段一并保留")


class TextConfig(ApiModel):
    """文本处理节点配置：固定的文本操作枚举。

    ``replace`` 必须填查找内容，``split`` 的分隔符不能为空（这两条在保存时即校验，
    避免运行期才报错）；``join`` 需要数组输入。文本节点不会执行任何代码或正则。
    """

    operation: Literal["stringify", "trim", "upper", "lower", "replace", "split", "join"] = Field(description="文本处理操作")
    find: str = Field(default="", max_length=2000, description="replace 的查找文本")
    replacement: str = Field(default="", max_length=4000, description="replace 的替换文本")
    separator: str = Field(default=",", max_length=100, description="split/join 分隔符")

    @model_validator(mode="after")
    def complete_operation(self):
        """保存时就补齐操作必需项：replace 要有查找内容，split 的分隔符不能为空。"""
        if self.operation == "replace" and not self.find:
            raise ValueError("文本替换必须填写查找内容")
        if self.operation == "split" and not self.separator:
            raise ValueError("文本拆分的分隔符不能为空")
        return self


class TemplateConfig(ApiModel):
    """模板渲染节点配置：只支持 ``{{ input }}`` 与 ``{{ $.path }}`` 的安全文本模板。

    渲染结果上限 512 KB；占位符在保存时逐个校验可编译，不做表达式求值。
    """

    template: str = Field(min_length=1, max_length=30000, description="仅支持 {{ input }} 与 {{ $.path }} 的安全文本模板")

    @field_validator("template")
    @classmethod
    def valid_template(cls, value: str) -> str:
        """文本模板只支持 ``{{ input }}`` 与 ``{{ $.path }}``，保存时逐个占位符校验。"""
        return safe_template(value)


class AggregateMetric(ApiModel):
    """聚合的一项指标：结果字段名 + 聚合方式 + 来源字段。

    ``count`` 可以不选字段（统计行数或非空值个数），其他运算必须选字段。
    """

    name: str = Field(min_length=1, max_length=120, description="聚合结果字段")
    operation: Literal["count", "sum", "avg", "min", "max", "count_distinct"] = Field(description="聚合方式")
    field: str = Field(default="", max_length=120, description="count 可留空，其他运算需选择字段")


class AggregatorConfig(ApiModel):
    """聚合节点配置：分组字段 + 至少一项聚合指标。

    约束：结果字段名不能覆盖分组字段（否则分组信息丢失）；除 count 外的运算必须选来源字段。
    """

    group_by: list[str] = Field(default_factory=list, alias="groupBy", max_length=20, description="分组字段；为空返回单行汇总")
    metrics: list[AggregateMetric] = Field(min_length=1, max_length=50, description="聚合指标")

    @model_validator(mode="after")
    def complete_aggregation(self):
        """聚合配置的交叉校验：分组字段与结果字段都要安全；结果名不能覆盖分组字段；
        除 count 外必须选来源字段；来源字段不得是原型污染名。
        """
        if self.group_by:
            validate_named_fields(self.group_by, "分组字段")
        names = validate_named_fields([metric.name for metric in self.metrics], "聚合结果字段")
        if set(names) & set(self.group_by):
            raise ValueError("聚合结果字段不能覆盖分组字段")
        if any(metric.operation != "count" and not metric.field.strip() for metric in self.metrics):
            raise ValueError("除计数外的聚合方式必须选择来源字段")
        if any(metric.field in _UNSAFE_NAMES for metric in self.metrics):
            raise ValueError("聚合来源字段不安全")
        return self


class IterationUpdate(ApiModel):
    """循环体的一处更新：字段名 + 有界算术公式。

    公式可引用当前字段以及约定的 ``_index``（从 0 开始）与 ``_iteration``（从 1 开始）；
    只允许算术运算，不执行代码。
    """

    name: str = Field(min_length=1, max_length=120, description="每轮写入的字段")
    formula: str = Field(min_length=1, max_length=2000, description="可引用当前字段以及 _index/_iteration 的有界算术公式")


class LoopConfig(ApiModel):
    """固定次数循环配置：``iterations`` 次，每次用公式更新或运行循环体子图。

    两种模式互斥：给了 ``subgraph`` 就忽略 ``updates``，否则必须配置 updates
    （校验器据此报"请配置循环体子图或更新公式"）。``keep_history`` 最多保留 100 份结果。
    ``_index``/``_iteration`` 是保留字段名，不能被更新公式占用。
    """

    iterations: int = Field(default=1, ge=1, le=100, description="固定循环次数")
    updates: list[IterationUpdate] = Field(default_factory=list, max_length=50, description="公式模式每轮计算的字段；子图模式可留空")
    keep_history: bool = Field(default=False, alias="keepHistory", description="是否保留每轮结果，最多 100 份")
    subgraph: FlowGraph | None = Field(default=None, description="可选循环体子图；每轮以当前对象作为开始节点输入")

    @model_validator(mode="after")
    def unique_updates(self):
        """公式模式下更新字段名必须唯一且不能占用 ``_index``/``_iteration``；且至少要有一项更新。

        子图模式下这两项都不要求（每轮由子图自己决定输出）。
        """
        if self.subgraph is None:
            validate_named_fields([item.name for item in self.updates], "循环更新字段", reserved={"_index", "_iteration"})
            if not self.updates:
                raise ValueError("请配置循环体子图或更新公式")
        return self


class IteratorConfig(ApiModel):
    """迭代配置：对上游数组逐项执行公式或子图。

    ``updates`` 为空且没有子图时只给每项加 ``_index``（合法用法，用于打标）。
    """

    updates: list[IterationUpdate] = Field(default_factory=list, max_length=50, description="对每项执行的计算；为空仅添加 _index")
    subgraph: FlowGraph | None = Field(default=None, description="可选迭代体子图；每项以当前记录作为开始节点输入")

    @model_validator(mode="after")
    def unique_updates(self):
        """迭代更新字段名必须唯一且不能占用保留字段；允许完全不给更新（仅打 _index）。"""
        if self.updates:
            validate_named_fields([item.name for item in self.updates], "迭代更新字段", reserved={"_index", "_iteration"})
        return self


class WhileConfig(ApiModel):
    """条件循环配置：每轮开始前判断表达式，为真才继续；``maxIterations`` 是硬上限。

    达到上限仍未终止会抛错（不会静默退出，也不会把未完成的结果当成完成）。
    与 loop 相同，``subgraph`` 与 ``updates`` 二选一。
    """

    expression: dict = Field(description="每轮开始前判断的布尔表达式")
    updates: list[IterationUpdate] = Field(default_factory=list, max_length=50, description="公式模式条件为真时更新的字段；子图模式可留空")
    max_iterations: int = Field(default=20, alias="maxIterations", ge=1, le=100, description="硬性最大循环次数")
    subgraph: FlowGraph | None = Field(default=None, description="可选条件循环体子图；每轮以当前对象作为开始节点输入")

    @model_validator(mode="after")
    def unique_updates(self):
        """公式模式下更新字段名必须唯一且不能占用保留字段；子图模式不要求更新项，
        但两种模式至少要有一种（否则循环体没有任何变化，必然达到最大次数或空转）。
        """
        if self.subgraph is None:
            validate_named_fields([item.name for item in self.updates], "条件循环更新字段", reserved={"_index", "_iteration"})
            if not self.updates:
                raise ValueError("请配置条件循环体子图或更新公式")
        return self


class ToolNodeConfig(ApiModel):
    """工具调用节点配置：指定工具网关中的已发布工具名与参数。

    参数会再经工具自身的 JSON Schema 校验（这里是第一道）；``includeInput`` 把节点输入
    以 ``input`` 参数合入。工具是否可执行由 ``graph.authorize_graph`` 与网关共同判定。
    """

    tool_name: str = Field(alias="toolName", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$", description="工具网关中的已发布工具名称，含 HTTP/MCP/内置工具")
    arguments: dict[str, Any] = Field(default_factory=dict, description="通过工具自身 JSON Schema 再次校验的参数")
    include_input: bool = Field(default=False, alias="includeInput", description="将节点输入以 input 参数合入工具参数")


class CodeConfig(ApiModel):
    """代码节点配置：受控沙箱内的 Python 代码与超时。

    代码通过内置工具 ``python_execute`` 执行，**始终禁用网络**；``inputs`` 是命名输入字典
    （默认有 ``input``），可通过设置 ``result`` 或定义 ``main(**inputs)`` 返回结果。
    代码节点不在自动重试白名单内（重跑可能有副作用）。
    """

    code: str = Field(min_length=1, max_length=50000, description="Python 代码；inputs 为命名输入字典，默认上游可用 input。设置 result 或定义 main(**inputs) 返回结果")
    timeout_seconds: int = Field(default=120, alias="timeoutSeconds", ge=5, le=300, description="沙箱执行超时")


class SubflowConfig(ApiModel):
    """子流程节点配置：固定子流程与其**当前乐观锁版本**，运行时会同时固定其发布修订。

    子流程必须在已保存的父执行中运行（``execution_id`` 为空时拒绝），
    递归调用在执行与恢复前都会被 ``_FLOW_STACK`` 拦截；子流程含写回时需要 ``confirmWrites``。
    """

    workflow_id: str = Field(alias="workflowId", min_length=1, max_length=26, description="已发布子流程")
    workflow_version: int = Field(alias="workflowVersion", ge=1, description="子流程当前乐观锁版本，运行时同时固定其发布修订")
    timeout_seconds: int = Field(default=120, alias="timeoutSeconds", ge=1, le=300, description="子流程超时")
    confirm_writes: bool = Field(default=False, alias="confirmWrites", description="子流程含实际写回时的显式确认")


class ParallelConfig(ApiModel):
    """并行分支配置：``branchNames`` 只是画布上的分支标签（去重后保存）。

    真正的并行是一次节点通过多条出线扇出到独立分支，同一输入按字节原样复制给各分支；
    汇合节点负责收口。这里不做并发调度配置。
    """

    branch_names: list[str] = Field(default_factory=list, alias="branchNames", max_length=20, description="画布分支标签；执行结果保持同一输入供多条下游连线使用")

    @field_validator("branch_names")
    @classmethod
    def unique_branch_names(cls, values: list[str]) -> list[str]:
        """分支标签去重并校验安全；空列表保持原样（标签仅用于画布展示）。"""
        return validate_named_fields(values, "并行分支标签") if values else values


class ExecutionSummary(BaseModel):
    """执行摘要（列表接口使用）：不含 snapshot/inputs/result，避免列表搬运大 JSON。

    ``status`` 的语义边界：``running/waiting/completed/failed/cancel_requested/cancelled``；
    **规则发现问题不等于执行失败**——运行成功只代表流程跑完，业务结论在报告里。
    ``version`` 是执行控制的乐观锁版本，恢复或取消后递增。
    """

    id: str = Field(description="工作流执行标识")
    workflowId: str = Field(description="流程标识")
    revision: int = Field(description="实际执行的不可变修订号")
    runId: str = Field(description="统一运行中心标识")
    version: int = Field(description="执行控制乐观锁版本；恢复或取消后递增")
    status: str = Field(description="running/waiting/completed/failed/cancel_requested/cancelled；规则发现问题不等于执行失败")
    errorMessage: str = Field(description="脱敏错误说明")
    createTime: str = Field(description="执行创建时间 UTC")
    durationMs: int = Field(description="已记录的流程耗时毫秒")


class ExecutionView(ExecutionSummary):
    """执行详情（含快照、真实输入与逐节点结果）。

    ``inputs`` 与 ``result`` 只在调用方具备执行记录与所引用数据源的读取权限时才会返回；
    权限被撤销后读取路径会直接拒绝，而不是返回脱敏后的部分数据。
    """

    snapshot: dict = Field(description="实际执行的名称、业务域和流程图快照")
    inputs: dict = Field(description="本次真实输入；须有执行记录和所引用数据源的读取权限")
    result: dict = Field(description="逐节点状态、结果、查询追溯标识与结束节点输出")
