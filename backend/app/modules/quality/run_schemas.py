"""Normal quality-run form contracts: no SQL, JSON paths or arbitrary request bodies.

职责：质检"普通运行页"的入参与出参契约 —— 用户只填样本清单（按设备标准组分组），
**不接触 SQL、JSONPath 或任意请求体**（本契约刻意不暴露这些字段，配置由发布流程固定）。

位置与依赖：由 ``quality.runs`` / ``quality.run_plans`` 与 ``quality.router`` 使用；
``QualityRunPlan`` 同时是"运行前预览"与向导返回的一部分（见 ``workflow_schemas``）。

约束：``version`` 与 ``publishedRevision`` 都必须明确 —— 前者是流程的乐观锁版本（过期拒绝新运行），
后者是用户已查看确认的发布修订（**不自动替换**为最新版）；
``idempotencyKey`` 只用于"不确定提交时重新查询"，不会触发重新检测。
"""
from pydantic import Field, field_validator, model_validator

from app.common.contracts import ApiModel
from app.modules.workflows.schemas import ExecutionSummary


class RunSample(ApiModel):
    """一个待检样本：用户填写的编号（组内唯一）与可选授权原图。

    样本编号是证据与报告的定位键，必须由用户明确填写（不自动生成）；
    ``imageFileId`` 仅外观检测需要，必须是本平台已授权的图片文件标识。
    """
    sampleId: str = Field(min_length=1, max_length=160, description="组内唯一、用户明确填写的样本编号")
    imageFileId: str | None = Field(default=None, min_length=1, max_length=26, description="外观检测所需的授权原图文件")

    @field_validator("sampleId")
    @classmethod
    def trim_sample(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入样本编号")
        return value.strip()


class RunGroup(ApiModel):
    """一个"设备标准组"的样本集合：绑定流程中的判定节点，样本编号组内不得重复。

    节点已固定设备与模板修订，因此同组样本共享同一套标准；
    **证据不跨设备/跨组混用**（组是按设备与标准划分的最小批次单位）。
    """
    nodeId: str = Field(min_length=1, max_length=80, description="所选已发布流程中的质检判定节点，绑定不可变设备与模板")
    samples: list[RunSample] = Field(min_length=1, max_length=100, description="此设备标准组的样本；不跨设备混用证据")

    @model_validator(mode="after")
    def unique_samples(self):
        if len({row.sampleId for row in self.samples}) != len(self.samples):
            raise ValueError("同一设备标准组的样本编号不能重复")
        return self


class QualityRunInput(ApiModel):
    """一次质检运行的完整表单：版本确认 + 分组样本 + 写回确认 + 超时预算。

    约束：``groups`` 必须与运行方案里的**全部**设备标准组对应（不能只跑一部分导致漏检），
    节点不得重复，样本总数上限 100（``bounded_batch``）；``confirmWrites`` 默认 False，
    有目标表写入时必须显式确认。``publishedRevision`` 是用户已确认的修订，
    服务端不会自动替换成最新发布版。
    """
    version: int = Field(ge=1, description="加载运行方案时的流程乐观锁版本，过期拒绝新运行")
    publishedRevision: int = Field(ge=1, description="用户已查看并确认的发布修订，不自动替换")
    idempotencyKey: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$", description="保留用于不确定提交的重查；不会自动重新检测")
    groups: list[RunGroup] = Field(min_length=1, max_length=30, description="必须与运行方案的全部设备标准组对应")
    confirmWrites: bool = Field(default=False, strict=True, description="有目标表写入时明确确认，默认不写入")
    timeoutSeconds: int = Field(default=120, ge=1, le=300, description="共享工作流总执行预算秒数")

    @model_validator(mode="after")
    def bounded_batch(self):
        if len({group.nodeId for group in self.groups}) != len(self.groups):
            raise ValueError("设备标准组不能重复")
        if sum(len(group.samples) for group in self.groups) > 100:
            raise ValueError("一次运行最多 100 个设备样本组合，请分批检测")
        return self


class QualityRunPlan(ApiModel):
    """运行前预览：要跑哪个修订、涉及哪些设备/标准/指标、会写入哪些目标表。

    ``publishedRevision`` 是**明确用于本次运行**的发布修订；``writes`` 列出目标表与逐字段映射，
    需用户单独确认（``QualityRunInput.confirmWrites``）。只读视图，不落库。
    """
    workflowId: str = Field(description="选择的共享工作流")
    name: str = Field(description="发布修订中的流程名称")
    version: int = Field(description="当前乐观锁版本")
    publishedRevision: int = Field(description="明确运行的发布修订")
    groups: list[dict] = Field(description="各判定节点绑定的设备、固定模板标准和实际检测指标")
    writes: list[dict] = Field(description="将执行的目标表、方式与逐字段映射，需单独确认")


class QualityRunView(ApiModel):
    """运行结果视图：共享工作流执行状态 + 每个设备样本组合的已保存报告。

    注意 ``execution`` 只是流程运行状态，**执行完成不代表样本合格**；
    失败时未生成报告的项会在 ``samples`` 中明确列出（不静默省略）。
    """
    execution: ExecutionSummary = Field(description="实际共享工作流状态；执行完成不代表样本合格")
    samples: list[dict] = Field(description="请求的全部设备样本组合及已保存报告；失败时未生成项明确列出")
