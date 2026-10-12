"""Strict request contracts for system operations and developer integrations.

中文补充（系统运维与开发者工具的请求契约）：
- 统一策略：凡是"改已有记录"的请求都必须带 version（乐观锁）；新建时 version 必须为 0。
- 字段约束承担安全职责：模板变量名、任务 code、公告受众的形状在入口即被限制，
  避免非法配置落库后才在运行期暴露。
- 依赖：app.common.contracts.ApiModel（统一 alias 与 extra 策略）；本模块无副作用。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.common.contracts import ApiModel


class TemplateInput(ApiModel):
    """通知模板的创建/更新请求（code 为小写点分标识，subject/body 用 {变量} 占位）。"""

    code: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_.-]+$", examples=["alert.critical"])
    name: str = Field(min_length=1, max_length=160, examples=["严重预警通知"])
    subject_template: str = Field(alias="subjectTemplate", min_length=1, max_length=300, examples=["设备 {device_name} 出现严重预警"])
    body_template: str = Field(alias="bodyTemplate", min_length=1, max_length=10000, examples=["规则 {rule_name} 已命中，请及时处理。"])
    variables: list[str] = Field(default_factory=list, max_length=40, examples=[["device_name", "rule_name"]])
    status: Literal["active", "disabled"] = "active"
    version: int = Field(default=0, ge=0, description="更新时必须填写当前版本；创建时为 0")

    @field_validator("variables")
    @classmethod
    def valid_variables(cls, value: list[str]) -> list[str]:
        """变量名去空白去重，且必须是合法标识符（字母/数字/下划线、不能以数字开头）。

        这样渲染时可直接按占位符名取值，无需再转义或求值表达式。
        """
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(not item.replace("_", "a").isalnum() or item[0].isdigit() for item in normalized):
            raise ValueError("变量名只能包含字母、数字和下划线，且不能以数字开头")
        return normalized


class NoticeInput(ApiModel):
    """系统公告的创建/更新请求（受众、发布时间与到期时间都在此约束）。"""

    title: str = Field(min_length=1, max_length=300, examples=["设备平台维护通知"])
    body: str = Field(min_length=1, max_length=30000, examples=["系统将于 22:00 进行维护，预计 30 分钟。"])
    notice_type: Literal["announcement", "maintenance", "security"] = Field(default="announcement", alias="noticeType")
    severity: Literal["info", "warning", "critical"] = "info"
    audience_type: Literal["all", "roles", "users"] = Field(default="all", alias="audienceType")
    audience_ids: list[str] = Field(default_factory=list, alias="audienceIds", max_length=500)
    publish_at: datetime | None = Field(default=None, alias="publishAt", description="留空表示由管理员手动发布")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_audience(self):
        """受众一致性校验：非 all 必须给出受众标识，all 不能同时给标识；到期必须晚于发布。

        这些是对外语义的硬约束，否则会出现"发给全部人却只填了某个角色"的歧义配置。
        """
        self.audience_ids = list(dict.fromkeys(self.audience_ids))
        if self.audience_type != "all" and not self.audience_ids:
            raise ValueError("指定角色或用户时至少选择一个受众")
        if self.audience_type == "all" and self.audience_ids:
            raise ValueError("全部成员受众不能同时提交受众标识")
        if self.publish_at and self.expires_at and self.expires_at <= self.publish_at:
            raise ValueError("到期时间必须晚于发布时间")
        return self


class VersionAction(ApiModel):
    """只带版本的通用动作请求（启用/暂停/归档/发布等状态变更的乐观锁入口）。"""

    version: int = Field(ge=1, examples=[1], description="当前记录版本，防止覆盖并发修改")


class JobInput(ApiModel):
    """计划任务创建/更新请求；任务类型决定 target_id 与 payload 的语义。"""

    code: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_.-]+$", examples=["alerts.hourly-check"])
    name: str = Field(min_length=1, max_length=200, examples=["每小时设备预警检查"])
    description: str = Field(default="", max_length=1000)
    task_type: Literal["workflow", "alert", "extraction", "notification", "health", "datasource_health"] = Field(alias="taskType")
    target_id: str = Field(default="", alias="targetId", max_length=80)
    schedule_kind: Literal["interval", "cron"] = Field(alias="scheduleKind")
    schedule_expression: str = Field(alias="scheduleExpression", min_length=1, max_length=160, examples=["3600"])
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict, description="工作流调度仅支持 published 模式；version 为保存时当前编辑版本，publishedRevision 固定已发布修订，省略时由服务端绑定当前发布修订。")
    status: Literal["active", "paused"] = "paused"
    version: int = Field(default=0, ge=0)


class JobRunInput(ApiModel):
    """手动触发计划任务的请求：版本乐观锁 + 调用方提供的幂等键。

    幂等键格式受限（字母数字与 _.:-），保证它既能作为唯一键也能安全出现在日志中。
    """

    version: int = Field(ge=1)
    idempotency_key: str = Field(
        alias="idempotencyKey", min_length=8, max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$",
        examples=["manual-alerts-20260901-01"],
    )


class BackupCreateInput(ApiModel):
    """创建整库备份请求：必须显式提交 confirmation=CREATE_BACKUP 以表达"只备份、不恢复"。"""

    name: str = Field(min_length=1, max_length=200, examples=["上线前完整备份"])
    confirmation: Literal["CREATE_BACKUP"] = Field(description="显式确认只创建备份，不执行恢复")


class IntegrationClientInput(ApiModel):
    """集成客户端创建/更新请求（redirect_uris 与 scopes 的白名单校验在服务层）。"""

    name: str = Field(min_length=1, max_length=160, examples=["设备门户"])
    redirect_uris: list[str] = Field(alias="redirectUris", default_factory=list, max_length=20, examples=[["https://portal.example.com/callback"]])
    scopes: list[str] = Field(default_factory=list, max_length=100, examples=[["alerts.view", "quality.view"]])
    enabled: bool = True
    version: int = Field(default=0, ge=0)


class ClientTokenInput(ApiModel):
    """换取访问令牌请求：客户端 ID 与密钥。

    ``repr=False`` 让密钥不进入对象 repr（避免日志/异常堆栈里意外打印）。
    本请求不需要登录上下文，因此服务层必须用常量时间比较校验密钥。
    """

    client_id: str = Field(alias="clientId", min_length=8, max_length=120, examples=["t5_exampleclient"])
    client_secret: str = Field(alias="clientSecret", min_length=24, max_length=200, repr=False)
    workspace_id: str = Field(alias="workspaceId", min_length=1, max_length=26)


class CertificateInput(ApiModel):
    """登记可信证书请求：只接受公开 PEM（含私钥会被服务层直接拒绝）。"""

    name: str = Field(min_length=1, max_length=200, examples=["客户设备网关 CA"])
    kind: Literal["trusted_ca", "client_signing"]
    pem: str = Field(min_length=64, max_length=100000, description="仅接受公开 X.509 证书 PEM；拒绝私钥")
    enabled: bool = True


class MigrationScanInput(ApiModel):
    """来源库只读扫描请求：来源库必须在服务端白名单内，include_tables 留空表示全部表。"""

    source_schema: str = Field(alias="sourceSchema", min_length=1, max_length=200, examples=["qknow_dev"])
    source_name: str = Field(alias="sourceName", min_length=1, max_length=200, examples=["智慧云脑 Cloud Brain 本机数据库"])
    include_tables: list[str] = Field(alias="includeTables", default_factory=list, max_length=300, description="留空扫描允许来源中的全部表")
    archive_rows: bool = Field(default=True, alias="archiveRows", description="保存脱敏来源行和校验摘要")


class ClientCodeInput(ApiModel):
    """SDK 生成请求：按 operationId 精确选取（最多 50 个），指定语言与是否附示例。"""

    operation_ids: list[str] = Field(alias="operationIds", min_length=1, max_length=50, examples=[["alerts_reports_list"]])
    language: Literal["python", "typescript"] = "python"
    include_examples: bool = Field(default=True, alias="includeExamples")
