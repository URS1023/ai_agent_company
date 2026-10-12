"""Typed, documented contracts for ordinary equipment and alert pages.

职责：设备台账（设备/测点/专家经验/预警策略）与问题处置的请求契约。
每个字段用 ``description`` 写清业务含义，前端表单提示直接取用这些文案。

位置与依赖：由 ``alerts.router`` 与 ``alerts.registry`` 使用；``Rule`` 复用共享工作流
规则表达式类型，``ApiModel`` 统一了别名与序列化口径。

约束：
- 所有可编辑对象都继承 ``Editable``：**修改必须携带读到的 ``version``**（乐观锁，过期 409）。
- 文本字段统一去首尾空白（``strip_text``），避免"看起来一样但被判不等"的重复数据。
- 本模块只做形状/取值校验，资源存在性与权限校验在 ``registry`` 的 ``_validate_references`` 中完成。
"""
from typing import Literal

from pydantic import Field, field_validator

from app.common.contracts import ApiModel
from app.modules.workflows.schemas import Rule


class Editable(ApiModel):
    """All updates require the caller's loaded optimistic concurrency version.

    （中文补充）可编辑对象的基类。``version`` 仅修改时必填；创建时留空。
    ``strip_text`` 对所有字符串字段做去空白，是**全局约定**而不是个别字段的特例：
    若某字段需要保留首尾空格（例如 SQL 片段），必须在此处显式豁免，
    否则会被静默裁掉。
    """

    version: int | None = Field(default=None, ge=1, description="修改时必填，使用读取到的版本，过期返回 409")

    @field_validator("*", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class DeviceInput(Editable):
    """设备台账的创建/修改契约（工作区内编码唯一，删除后编码保留供追溯）。

    关键约束：``domains`` 至少一个（alert 或 quality），决定设备能参与哪些业务；
    ``quality_datasource_ids`` 是获准使用的**外观/成分检测 HTTP 服务**白名单（去重、最多 20 个），
    由注册表侧再校验 provider 必须为 http；``protocol`` 只是协议标识，
    填了 HTTP/数据库并不代表适配器已联通，实际采集能力以数据源配置为准。
    ``status`` 为 inactive/maintenance 时不允许开始新的检测（历史仍可读）。
    """
    code: str = Field(min_length=1, max_length=80, description="工作区内唯一设备编码；删除后也保留编码供追溯")
    name: str = Field(min_length=1, max_length=160, description="设备名称")
    category: str = Field(default="", max_length=100, description="设备类型")
    model: str = Field(default="", max_length=100, description="设备型号")
    manufacturer: str = Field(default="", max_length=160, description="生产商")
    location: str = Field(default="", max_length=300, description="安装位置")
    protocol: str = Field(default="HTTP", min_length=1, max_length=40, description="协议标识；HTTP/数据库由共享源执行，其他协议不冒充已联通")
    datasource_id: str | None = Field(default=None, alias="datasourceId", min_length=1, max_length=26, description="已有授权采集源；凭据在数据源中心配置")
    quality_datasource_ids: list[str] = Field(
        default_factory=list, alias="qualityDatasourceIds", max_length=20,
        description="此设备获准使用的外观/成分 HTTP 检测服务；每个工作流指标仍固定具体服务版本",
    )
    domains: list[Literal["alert", "quality"]] = Field(default_factory=lambda: ["alert", "quality"], min_length=1, max_length=2, description="设备可复用业务")
    description: str = Field(default="", max_length=3000, description="设备备注")
    status: Literal["active", "inactive", "maintenance"] = Field(default="active", description="在用、停用或维护；后两者不启动新检测")

    @field_validator("quality_datasource_ids")
    @classmethod
    def distinct_quality_sources(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(not 1 <= len(value) <= 26 for value in values):
            raise ValueError("质检服务标识无效或重复")
        return values


class PointInput(Editable):
    """测点契约：把"采集结果里的完整字段名"映射为一个具名测点。

    ``field_name`` 必须与采集结果字段完全一致（向导据此生成 JSONPath），
    同一设备内 ``code`` 唯一；修改时**不得更换所属设备**（注册表会显式拒绝）。
    ``frequency_minutes`` 只是期望同步间隔，保存台账不会自动创建调度任务。
    """
    device_id: str = Field(alias="deviceId", min_length=1, max_length=26, description="所属设备；修改时不得更换设备")
    code: str = Field(min_length=1, max_length=80, description="设备内唯一测点号")
    name: str = Field(min_length=1, max_length=160, description="测点名称")
    field_name: str = Field(alias="fieldName", min_length=1, max_length=120, description="采集结果中对应的完整字段名")
    unit: str = Field(default="", max_length=40, description="计量单位")
    point_type: str = Field(default="number", alias="pointType", min_length=1, max_length=40, description="测点类型")
    device_key: str = Field(default="", alias="deviceKey", max_length=120, description="来源系统设备 key")
    prefix: str = Field(default="", max_length=120, description="来源系统前缀")
    frequency_minutes: int | None = Field(default=None, alias="frequencyMinutes", ge=1, le=525600, description="期望同步间隔；保存台账不会自动启用调度")
    failure_diagnosis: bool = Field(default=False, alias="failureDiagnosis", description="是否用于故障诊断")
    description: str = Field(default="", max_length=3000, description="测点说明")
    status: Literal["active", "inactive"] = Field(default="active", description="测点是否启用")


class AdviceInput(Editable):
    """专家经验契约：工况 + 告警条件 + 处置预案，是"经验知识"而非判定依据。

    保存策略时可关联一条经验，关联后修改策略会重新快照经验内容；
    因此事后编辑这里的文本不会改变已发布流程的判据与预案。
    """
    name: str = Field(min_length=1, max_length=160, description="专家经验名称")
    condition_code: str = Field(default="", alias="conditionCode", max_length=80, description="工况编码")
    operating_condition: str = Field(default="", alias="operatingCondition", max_length=300, description="适用工况")
    alert_type: str = Field(default="", alias="alertType", max_length=80, description="告警类型")
    condition_description: str = Field(default="", alias="conditionDescription", max_length=3000, description="告警条件说明")
    content: str = Field(default="", max_length=10000, description="专家经验内容")
    response_plan: str = Field(default="", alias="responsePlan", max_length=10000, description="处置预案")
    plan_short: str = Field(default="", alias="planShort", max_length=200, description="预案简称")
    status: Literal["active", "inactive"] = Field(default="active", description="经验是否启用")


class AlertRuleInput(Editable):
    """预警策略契约：绑定受检设备、逐项异常条件、可选的专家经验。

    ``device_id`` 必须拥有读取权限且设备须启用 alert 用途；
    ``record_key_field`` 决定问题如何定位到源记录（留空则用报告行号），
    填写后若实际数据缺少该字段，判定会失败而不是生成不完整报告。
    ``rules`` 使用共享工作流规则表达式，规则项标识不得重复、标识与名称不得为空白。
    ``status`` 为 inactive 时禁止开始新的预警，历史报告仍可追溯。
    """
    name: str = Field(min_length=1, max_length=160, description="预警策略名称")
    description: str = Field(default="", max_length=3000, description="规则用途")
    device_id: str = Field(alias="deviceId", min_length=1, max_length=26, description="受检设备，须拥有读取权限")
    advice_id: str | None = Field(default=None, alias="adviceId", min_length=1, max_length=26, description="关联专家经验；保存规则时固化经验内容")
    record_key_field: str = Field(default="", alias="recordKeyField", max_length=120, description="用于定位问题记录的字段；填写后缺失该字段将失败")
    rules: list[Rule] = Field(min_length=1, max_length=100, description="逐项异常判断；全部使用共享工作流规则表达式")
    status: Literal["active", "inactive"] = Field(default="active", description="停用后禁止开始新预警，历史仍可追溯")


class ProblemAction(ApiModel):
    """问题处置请求：明确的版本 + 目标状态 + 必填理由。

    ``status`` 四态语义见 ``models.AlertProblem``：``open`` 待处理（也用于"重新打开"）、
    ``acknowledged`` 已知悉、``resolved`` 已解决、``ignored`` 已忽略。
    后端**不限制状态迁移方向**，任意状态可互转，靠 ``note`` 必填与事件流水约束；
    ``version`` 过期返回 409（防止覆盖他人处置）。处置不会修改原始判定，也不会删除问题。
    注意本模型**不继承** ``Editable``：这里必须显式携带版本，不允许"不填版本就更新"。
    """
    version: int = Field(ge=1, description="读取到的问题版本，防止覆盖他人处置")
    status: Literal["open", "acknowledged", "resolved", "ignored"] = Field(description="重新打开、确认、已解决或忽略")
    note: str = Field(min_length=1, max_length=3000, description="必填处置理由；不会修改原始判定或删除问题")

    @field_validator("note")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入处置理由")
        return value.strip()
