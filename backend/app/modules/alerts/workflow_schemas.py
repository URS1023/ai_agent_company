"""Customer-facing contracts for publishing a complete equipment alert workflow.

职责：设备预警向导的请求/响应契约。向导只暴露"业务看得懂"的字段（策略修订、测点、可选计算、
可选通知/写回/调度），由 ``alerts.workflow_builder`` 编译成完整画布。

位置与依赖：被 ``alerts.router`` 的向导接口与 ``workflow_builder`` 使用；直接复用
``workflows.schemas`` 的 ``CalculatedField`` / ``WorkflowView`` 与 ``writeback`` 的
``OutputTableConfig``，保证"向导创建的流程"与"手工编辑的流程"用同一套字段语义。

约束：本模块只做形状与取值范围校验（含跨字段的节点数预算），
**不做权限、资源存在性或授权校验** —— 那些一律在 ``workflow_builder`` 的预检阶段完成，
因此不要在这里加数据库访问。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.common.contracts import ApiModel
from app.modules.workflows.schemas import CalculatedField, WorkflowView
from app.modules.writeback.schemas import OutputTableConfig


class AlertSourceInput(ApiModel):
    """采集来源选择：设备已绑定的数据源 + 用户核对过的配置版本。

    ``datasource_version`` 是防止"用户看的是旧配置、提交的是新配置"的乐观锁：
    版本不符时向导抛 409 要求刷新重核，而不是静默按最新配置发布。
    数据库来源必须给单条只读 SELECT 与（可选）命名参数；HTTP 来源两者都必须为空。
    """
    datasource_id: str = Field(
        alias="datasourceId", min_length=1, max_length=26,
        description="设备当前明确绑定的 HTTP 或关系数据库数据源",
    )
    datasource_version: int = Field(
        alias="datasourceVersion", ge=1,
        description="用户核对过的连接、HTTP 请求体与 JSONPath 配置版本",
    )
    query: str = Field(
        default="", max_length=30000,
        description="关系数据库的单条只读 SELECT；HTTP 来源不填写",
    )
    parameters: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, description="SQL 命名参数值；HTTP 来源必须为空",
    )
    max_rows: int = Field(default=1000, alias="maxRows", ge=1, le=10000)

    @field_validator("query")
    @classmethod
    def trim_query(cls, value: str) -> str:
        return value.strip()


class AlertNotificationInput(ApiModel):
    """站内通知配置：投递给谁、哪些判定结论才发、问题的最低等级门槛。

    ``outcomes`` 默认只通知"发现问题"和"无数据"，**默认不通知 normal**（避免"一切正常"刷屏）；
    重复值会被去重而不是报错，保持幂等友好。
    """
    audience: Literal["executor", "all"] = Field(
        default="executor", description="通知本次执行人或工作区全部启用成员",
    )
    outcomes: list[Literal["issues", "no_data", "normal"]] = Field(
        default_factory=lambda: ["issues", "no_data"], min_length=1, max_length=3,
        description="哪些实际判定结果生成站内消息",
    )
    minimum_severity: Literal["info", "warning", "critical"] = Field(
        default="info", alias="minimumSeverity", description="发现问题时的最低通知等级",
    )

    @field_validator("outcomes")
    @classmethod
    def unique_outcomes(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class AlertScheduleInput(ApiModel):
    """可选固定调度：间隔秒数或五段 cron，默认时区 Asia/Shanghai。

    ``status`` 默认 ``paused``：**创建向导时默认不排期**，需要用户显式改为 ``active`` 才会
    计算 ``next_run_at``，避免误配一个立即开始跑的定时任务。
    """
    schedule_kind: Literal["interval", "cron"] = Field(alias="scheduleKind")
    schedule_expression: str = Field(
        alias="scheduleExpression", min_length=1, max_length=160,
        description="间隔秒数（60–31536000）或五段 cron",
    )
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    status: Literal["active", "paused"] = Field(default="paused")

    @field_validator("schedule_expression", "timezone")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()


class AlertWorkflowInput(ApiModel):
    """向导提交的完整表单 —— 一次提交即一次"创建并发布"。

    ``request_key`` 由客户端生成（``[A-Za-z0-9_-]{8,100}``），用于网络重试时避免重复建流程：
    同键同配置返回既有结果，同键不同配置返回 409。
    测点必须显式选择（不采用默认测点），且不得重复；``rule_revision`` 必须明确，
    因为发布后的流程将长期固定该修订，后续编辑策略不会影响已发布流程的判定口径。
    """
    request_key: str = Field(
        alias="requestKey", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$",
        description="防止网络不确定时重复创建；同键不同配置返回 409",
    )
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    rule_id: str = Field(alias="ruleId", min_length=1, max_length=26)
    rule_revision: int = Field(alias="ruleRevision", ge=1, description="明确固定的策略修订")
    source: AlertSourceInput
    confirm_http_writes: bool = Field(
        default=False, strict=True, alias="confirmHttpWrites",
        description="明确允许固定来源版本的非 GET 请求产生外部副作用；独立于自动采集与数据库写回许可",
    )
    point_ids: list[str] = Field(
        alias="pointIds", min_length=1, max_length=200,
        description="用户手动选择的测点；系统不采用默认测点",
    )
    calculations: list[CalculatedField] = Field(
        default_factory=list, max_length=100,
        description="字段提取后的有界加减乘除计算，后项可引用前项结果",
    )
    notification: AlertNotificationInput | None = Field(
        default=None, description="可选站内预警通知；不配置则只生成报告和问题",
    )
    writeback: OutputTableConfig | None = Field(
        default=None, description="可选授权目标表写入；运行时必须明确确认",
    )
    schedule: AlertScheduleInput | None = Field(
        default=None, description="可选固定调度；创建即发布，是否立即启用由状态决定",
    )

    @field_validator("name")
    @classmethod
    def required_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请输入预警流程名称")
        return value

    @field_validator("description")
    @classmethod
    def trim_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("point_ids")
    @classmethod
    def unique_points(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("测点不能重复选择")
        return values

    @model_validator(mode="after")
    def graph_budget(self):
        """预算校验：向导生成的画布节点数由可选块决定，超上限直接拒绝表单。

        计数口径必须与 ``workflow_builder._build_graph`` 的节点白名单保持一致
        （start/collect/extract/evaluate/end 共 5 个固定节点，另加可选的计算、通知、写回）。
        当前上限 100 远大于可能值，实际是防"未来新增可选块后忘记收敛"的兜底；
        调整固定节点数量时必须同步更新这里的基数。
        """
        node_count = 5 + int(bool(self.calculations)) + int(self.notification is not None) + int(self.writeback is not None)
        if node_count > 100:
            raise ValueError("生成后的流程超过 100 个节点")
        return self


class AlertScheduleView(BaseModel):
    """向导返回的调度摘要（只读视图，字段名已是前端驼峰形式）。

    直接来自 ``system_tools.schedules.job_view`` 的键名约定；``version`` 是调度的乐观锁版本，
    用户后续暂停/修改调度时需带回。
    """
    id: str
    code: str
    name: str
    taskType: str
    targetId: str
    scheduleKind: str
    scheduleExpression: str
    timezone: str
    status: str
    nextRunAt: str | None
    version: int


class AlertWorkflowView(ApiModel):
    """向导的返回体：已发布流程 + 可选调度 + 实际提取字段 + 是否复用了既有结果。

    ``reused=True`` 表示本次是幂等重放（未新建任何资源），前端应提示"已存在相同流程"，
    不要再次提示创建成功。
    """
    workflow: WorkflowView = Field(description="已创建并发布的共享工作流")
    schedule: AlertScheduleView | None = Field(description="可选固定调度")
    selected_fields: list[str] = Field(alias="selectedFields", description="实际提取并参与规则的字段")
    reused: bool = Field(description="是否由相同防重复键返回既有结果")
