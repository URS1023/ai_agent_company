"""Versioned HTTP source, JSONPath extraction and batch execution contracts."""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.common.contracts import ApiModel
from app.modules.analytics.schemas import QueryResult


# 单个输出字段的提取规则：name 唯一，path 是相对一条记录的 JSONPath，type 决定
# 是否做类型转换（默认 auto 不转换，避免把编号类字符串变成数字）。
class ExtractField(ApiModel):
    name: str = Field(min_length=1, max_length=120, description="输出字段名；在一条记录中必须唯一")
    path: str = Field(default="$", min_length=1, max_length=500, description="相对于一条记录的 JSONPath，例如 $.temperature")
    type: Literal["auto", "string", "decimal", "integer", "boolean", "json"] = Field(default="auto", description="明确的字段类型；金额用 decimal，不隐式把编号转为数值")
    required: bool = Field(default=False, description="必填字段缺失或为空时，此条请求判定失败并记录错误")
    multiple: bool = Field(default=False, description="允许路径匹配多个值并输出数组；未启用而匹配多值时拒绝截取首条")
    comment: str = Field(default="", max_length=500, description="字段业务注释，可用于表格、分析和后续规则选择")

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        """字段名去空格并拒绝空值、原型污染键名与控制字符（字段名会进入结果对象）。"""
        value = value.strip()
        if not value or value in {"__proto__", "constructor", "prototype"} or any(ord(c) < 32 for c in value):
            raise ValueError("字段名为空或不安全")
        return value

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        """保存前就用同一编译器校验 JSONPath，把语法错误拦在配置阶段而不是采集阶段。"""
        from app.modules.collection.extraction import compile_path
        compile_path(value)
        return value.strip()


# GET 分页规则：同一地址按指定查询参数递增，页数上限 20，受整批超时与结果上限约束。
class HttpPagination(ApiModel):
    parameter: str = Field(default="start", min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$", description="固定地址中递增的查询参数")
    start: int = Field(default=0, ge=0, le=10000000)
    step: int = Field(default=25, ge=1, le=100000)
    pages: int = Field(default=1, ge=1, le=20, description="明确限定请求页数；最多 20 页")


# HTTP 采集配置的写模型：地址固定（不允许跳转/模板变量/UUID 占位），方法只开放 GET/POST，
# 凭据只写不读（credentials 省略=保留，{} = 显式清空），敏感头部/字段必须整体引用凭据。
# 允许内网目标（allow_private）与自动调用（allow_automatic）都需要额外权限，见 sources.validate_authority。
class HttpSourceInput(ApiModel):
    name: str = Field(min_length=1, max_length=200, description="HTTP 数据源业务名称")
    url: str = Field(min_length=1, max_length=2000, description="固定 HTTP(S) 查询地址；不允许 URL 凭据、密钥、模板变量或跳转")
    method: Literal["GET", "POST"] = Field(default="GET", description="采集方法；不开放 PUT/PATCH/DELETE")
    response_format: Literal["json", "text"] = Field(default="json", alias="responseFormat", description="JSON 记录或网页/纯文本；文本输出 body、contentType、url")
    pagination: HttpPagination | None = Field(default=None, description="GET 同一地址按查询参数递增分页，受整批超时和结果限制")
    headers: dict[str, str] = Field(default_factory=dict, description="请求头；敏感值使用 ${secret.名称} 引用，不保存明文认证值")
    bodies: list[Any] = Field(default_factory=lambda: [None], min_length=1, max_length=100, description="单条或批量 JSON 请求体；GET 只能为 [null]；质检动态 POST 可完整引用 ${input.sampleId}、${input.imageBase64} 等已登记参数，不能动态更改目标或请求头")
    record_path: str = Field(default="$", alias="recordPath", min_length=1, max_length=500, description="从响应选择记录或数组的 JSONPath，例如 $.data.items[*]")
    fields: list[ExtractField] = Field(default_factory=list, max_length=200, description="勾选的输出字段及 JSONPath；空列表自动保留记录的所有字段（最多 200）")
    credentials: dict[str, str] | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True}, description="只写的加密凭据映射；省略或 null 保留，{} 显式清空；只返回名称不返回值")
    allow_private: bool = Field(default=False, alias="allowPrivate", description="允许指定的内网/本机设备地址；设置及变更目标需要 system.admin，元数据地址始终禁止")
    allow_automatic: bool = Field(default=False, alias="allowAutomatic", description="确认该接口只读取数据并允许报表/工作流自动调用；POST 启用或修改需 datasources.publish")
    timeout_seconds: int = Field(default=15, alias="timeoutSeconds", ge=1, le=60, description="单条请求最大秒数；整批另受 batchTimeoutSeconds 限制")
    batch_timeout_seconds: int = Field(default=60, alias="batchTimeoutSeconds", ge=1, le=120, description="整批采集最大秒数；未开始的请求明确标记为未执行")
    max_rows: int = Field(default=1000, alias="maxRows", ge=1, le=10000, description="整批合并结果最多返回的记录数；达到上限后不再发送剩余请求")
    continue_on_error: bool = Field(default=False, alias="continueOnError", description="批量中单条失败是否继续；失败不自动重试，部分结果明确标为部分成功")
    enabled: bool = Field(default=True, description="是否允许新的采集；停用仍保留历史结果")
    description: str = Field(default="", max_length=2000, description="数据用途、接口只读约定、字段口径和设备说明")
    version: int | None = Field(default=None, ge=1, description="修改必须提交加载时的版本，冲突返回 409")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        """名称去空格并拒绝纯空白，避免出现界面上不可见的数据源。"""
        if not value.strip():
            raise ValueError("请输入数据源名称")
        return value.strip()

    @field_validator("record_path")
    @classmethod
    def valid_record_path(cls, value: str) -> str:
        """保存前校验记录 JSONPath，避免把语法错误保存进配置。"""
        from app.modules.collection.extraction import compile_path
        compile_path(value)
        return value.strip()

    @field_validator("credentials")
    @classmethod
    def valid_credentials(cls, values):
        """凭据名与值都受限：最多 30 项、名称按标识符规则、值 1–4096 字符。

        限制是为了让凭据可以被安全地引用与替换，不能靠它承载大文件或任意结构。
        """
        if values is not None:
            if len(values) > 30 or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,59}", key)
                                       or not value or len(value) > 4096 for key, value in values.items()):
                raise ValueError("凭据最多 30 项；名称用字母、数字和下划线，值为 1–4096 字符")
        return values

    @model_validator(mode="after")
    def valid_configuration(self):
        """跨字段校验：分页只支持 GET、GET 不能带请求体、字段名唯一、请求体可序列化。

        同时复用传输层与参数层的校验（URL、请求头、请求体凭据、动态参数），
        因此“保存成功”等价于“配置可在允许的边界内执行”，而不是等到发请求才报错。
        """
        if self.pagination and self.method != "GET":
            raise ValueError("按查询参数分页仅支持 GET")
        if self.method == "GET" and self.bodies != [None]:
            raise ValueError("GET 不发送请求体，只能配置 [null]；批量请求体请选择 POST")
        if len({item.name for item in self.fields}) != len(self.fields):
            raise ValueError("输出字段名不能重复")
        try:
            encoded = json.dumps(self.bodies, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("请求体必须是有限、可序列化的 JSON") from exc
        if len(encoded.encode("utf-8")) > 1024 * 1024:
            raise ValueError("全部请求体不能超过 1 MB")
        from app.modules.collection.transport import validate_url, validate_headers, validate_body_secrets
        validate_url(self.url)
        validate_headers(self.headers)
        validate_body_secrets(self.bodies)
        from app.modules.collection.parameters import parameter_names
        parameter_names(self.bodies)
        if parameter_names(self.headers):
            raise ValueError("动态样本参数不能修改请求头")
        return self


# HTTP 配置的读模型：只回传凭据名称与是否存在，永不返回值；version 用于并发编辑。
class HttpSourceView(BaseModel):
    id: str = Field(description="统一智慧云脑 Cloud Brain 数据源标识")
    name: str = Field(description="数据源名称")
    type: Literal["http"] = Field(default="http", description="数据源类型")
    url: str = Field(description="不含明文凭据的固定目标地址")
    method: str = Field(description="GET 或 POST")
    responseFormat: Literal["json", "text"] = "json"
    pagination: HttpPagination | None = None
    headers: dict[str, str] = Field(description="只包含公开值和凭据占位符的请求头")
    bodies: list[Any] = Field(description="保存的单条/批量请求体，凭据仅保留引用")
    recordPath: str = Field(description="记录提取路径")
    fields: list[ExtractField] = Field(description="已选择的输出字段")
    credentialNames: list[str] = Field(description="已配置的凭据名称，不返回其值")
    hasCredentials: bool = Field(description="是否存在加密凭据")
    allowPrivate: bool = Field(description="是否授权访问此内网目标")
    allowAutomatic: bool = Field(description="是否允许无交互的只读采集")
    timeoutSeconds: int = Field(description="单条请求超时")
    batchTimeoutSeconds: int = Field(description="整批超时")
    maxRows: int = Field(description="最大结果行数")
    continueOnError: bool = Field(description="批量单条失败时是否继续")
    enabled: bool = Field(description="是否启用")
    description: str = Field(description="业务说明")
    version: int = Field(description="当前配置版本")
    createTime: str = Field(description="创建时间 UTC")
    updateTime: str = Field(description="修改时间 UTC")


# 手工采集入参：version 过期时不发送任何请求；confirmed 只对本次 POST 生效，
# 不会让数据源获得长期自动调用权限（那是 allowAutomatic）。
class CollectInput(ApiModel):
    version: int = Field(ge=1, description="用户已确认的当前配置版本；过期时不发送请求")
    confirmed: bool = Field(default=False, description="手动采集明确确认已保存的 POST 请求；不使数据源获得自动调用权限")


# 大屏 HTTP 组件预览入参：数据源必须已授权自动只读采集（allowAutomatic）。
class HttpWidgetPreview(ApiModel):
    datasource_id: str = Field(alias="datasourceId", min_length=1, max_length=26, description="已授权且允许自动只读采集的 HTTP 数据源")


# 单个请求体的执行结果：index 是已保存请求列表中的零基序号，status=skipped 表示
# 因整批超时或已达行数上限而未发送（不等于失败），message 不含响应正文。
class RequestOutcome(BaseModel):
    index: int = Field(description="请求体在已保存列表中的零基序号")
    status: Literal["success", "failed", "skipped"] = Field(description="成功、失败或未发送请求")
    rows: int = Field(default=0, description="此请求实际贡献的记录数")
    executionTime: int = Field(default=0, description="请求及提取耗时毫秒")
    httpStatus: int | None = Field(default=None, description="上游实际 HTTP 状态，未获得响应时为空")
    responseBytes: int = Field(default=0, description="接收的响应字节数")
    truncated: bool = Field(default=False, description="此请求还有未返回记录")
    message: str = Field(default="", description="脱敏失败或未执行原因，不含响应正文")


# 采集结果：复用查询结果契约但固定 queryType=http、generatedSql 为空（HTTP 不执行 SQL），
# rowOrigins 与 data 一一对应记录每行来自第几个请求体，便于导出与追溯来源。
class CollectionResult(QueryResult):
    queryType: Literal["http"] = Field(default="http", description="固定为 HTTP，不冒充 SQL 查询")
    generatedSql: str = Field(default="", description="HTTP 不执行 SQL，始终为空")
    status: Literal["success", "partial", "failed"] = Field(description="整批成功、部分完成或全部失败")
    outcomes: list[RequestOutcome] = Field(description="每个配置请求体的实际状态，包含未发送项")
    rowOrigins: list[int] = Field(description="与 data 一一对应的请求体序号，保留批量来源")


# 离线提取预览入参：只解析用户主动提供的样本，不发送任何网络请求、不写入执行历史。
class ExtractionPreview(ApiModel):
    sample: Any = Field(description="用户主动提供的 JSON 响应样本，仅预览提取，不发送网络请求")
    record_path: str = Field(default="$", alias="recordPath", max_length=500, description="记录或记录数组 JSONPath")
    fields: list[ExtractField] = Field(default_factory=list, max_length=200, description="要保留的字段及类型")
    max_rows: int = Field(default=20, alias="maxRows", ge=1, le=100, description="离线预览最多行数")

    @field_validator("record_path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        """预览也用同一编译器校验记录 JSONPath，保证预览与真实采集行为一致。"""
        from app.modules.collection.extraction import compile_path
        compile_path(value)
        return value.strip()
