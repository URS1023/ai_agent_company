"""Customer-facing quality workflow assembly contracts without raw graph JSON.

职责：质检向导的请求/响应契约 —— 用户按"设备 + 模板修订 + 每个自动指标的检测服务"
组织分组，由 ``quality.workflow_builder`` 编译成完整画布；**不暴露原始图 JSON**
（用户不需要、也不应该手改画布）。

位置与依赖：复用 ``workflows.schemas.WorkflowView``、
``writeback.schemas.OutputTableConfig`` 与 ``run_schemas.QualityRunPlan``，
使"向导发布的流程"与"运行页看到的计划"用同一套字段。

约束：只做形状与跨字段校验（含节点数预算），资源存在性、版本一致性与权限全部由
``workflow_builder`` 预检；同组内指标不得重复绑定。
"""
from pydantic import Field, field_validator, model_validator

from app.common.contracts import ApiModel
from app.modules.quality.run_schemas import QualityRunPlan
from app.modules.workflows.schemas import WorkflowView
from app.modules.writeback.schemas import OutputTableConfig


class DetectorBindingInput(ApiModel):
    """一个自动指标到检测服务的绑定：指标（含版本）+ HTTP 检测服务（含配置版本）。

    两个版本都必须固定并经过用户核对：运行时会与实际版本比对，不一致即拒绝执行，
    避免"改了服务请求体后旧流程静默按新配置跑"。
    """
    metric_id: str = Field(alias="metricId", min_length=1, max_length=26, description="模板修订中的明确指标")
    metric_version: int = Field(alias="metricVersion", ge=1, description="模板修订固定的指标版本")
    datasource_id: str = Field(alias="datasourceId", min_length=1, max_length=26, description="设备已授权的 HTTP 检测服务")
    datasource_version: int = Field(alias="datasourceVersion", ge=1, description="已核对的连接、请求体和 JSONPath 配置版本")


class WorkflowGroupInput(ApiModel):
    """一个设备标准组：设备 + 明确选定的模板修订 + 每个自动指标的检测服务绑定。

    模板必须由用户手动选择（无默认模板），修订号也必须明确；同组内同一指标不能重复绑定
    （``unique_metrics`` 校验），否则同一指标会出现两套检测来源。
    """
    title: str = Field(default="", max_length=160, description="业务组名称；空时使用设备与模板名称")
    device_id: str = Field(alias="deviceId", min_length=1, max_length=26, description="一个启用质检用途的设备")
    template_id: str = Field(alias="templateId", min_length=1, max_length=26, description="用户手动选择的检验模板；无默认模板")
    template_revision: int = Field(alias="templateRevision", ge=1, description="用户手动确认的不可变模板修订")
    detectors: list[DetectorBindingInput] = Field(min_length=1, max_length=100, description="模板每个自动指标对应的实际检测服务")

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def unique_metrics(self):
        if len({item.metric_id for item in self.detectors}) != len(self.detectors):
            raise ValueError("同一设备标准组不能重复绑定质检指标")
        return self


class QualityWorkflowInput(ApiModel):
    """质检向导的完整表单：一次提交即"创建并发布"。

    ``request_key`` 由客户端生成，用于网络重试时避免重复建流程（同键同配置复用既有结果，
    同键不同配置 409）；写入配置可选，但运行时仍需逐批显式确认（本处只是声明目标表）。
    """
    request_key: str = Field(
        alias="requestKey", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$",
        description="防止网络不确定时重复创建流程；同键不同配置返回 409",
    )
    name: str = Field(min_length=1, max_length=200, description="检测流程名称")
    description: str = Field(default="", max_length=2000, description="适用产品、产线与团队说明")
    groups: list[WorkflowGroupInput] = Field(min_length=1, max_length=30, description="设备、固定标准及逐指标检测服务")
    writeback: OutputTableConfig | None = Field(default=None, description="可选的授权报告结果写入；运行时仍需逐批确认")

    @field_validator("name")
    @classmethod
    def required_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请输入检测流程名称")
        return value

    @field_validator("description")
    @classmethod
    def trim_description(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def graph_budget(self):
        """预算校验：按"每组一个判定节点 + 每个检测绑定一个节点 + 合并/结束节点"估算生成后的画布规模。

        计数口径必须与 ``quality.workflow_builder`` 的建图逻辑保持一致，
        否则这里放过的表单会在建图阶段超限（或反之误拒）。超限提示拆分多个检测流程。
        """
        detector_count = sum(len(group.detectors) for group in self.groups)
        node_count = 2 + detector_count + len(self.groups)
        node_count += sum(len(group.detectors) > 1 for group in self.groups)
        node_count += int(len(self.groups) > 1) + int(self.writeback is not None)
        if node_count > 100:
            raise ValueError("生成后的流程超过 100 个节点，请拆分为多个检测流程")
        return self


class QualityWorkflowView(ApiModel):
    """向导返回体：已发布流程 + 运行计划预览 + 是否复用了既有创建结果。

    ``reused=True`` 表示本次是幂等重放（未新建任何资源），前端应提示"已存在相同流程"。
    """
    workflow: WorkflowView = Field(description="已创建并发布的正常共享工作流")
    runPlan: QualityRunPlan = Field(description="普通质检运行页将展示的设备、标准、指标与写入计划")
    reused: bool = Field(description="是否由相同防重复键返回既有创建结果")
