"""Workflow drafts, immutable published revisions and idempotent execution history.

中文补充：本模块的四张表是工作流数据模型的核心，理解它们的职责边界很重要：

- ``workflow_definitions``：**可变草稿**（graph 列）加上"当前正式运行的修订号"。
  编辑永远只改草稿；``published_revision`` 指向不可变的 ``workflow_revisions`` 行，
  因此编辑草稿不会影响正在被设备调度运行的那一版。
- ``workflow_revisions``：不可变快照（名称、用途、业务域、完整图、资源引用），
  每次保存/发布/恢复都会追加一行，供历史查看、恢复与"运行固定版本"使用。
- ``workflow_business_states``：流程级持久业务状态（采集窗口水位线、扫描游标、
  通知去重指纹、AI 分析缓存），按 ``(workspace, workflow, state_key)`` 唯一。
  这张表只保存"进度"，绝不修改来源业务数据。
- ``workflow_executions``：一次真实运行，含图快照、真实输入、逐节点结果与乐观锁 ``version``；
  ``(workspace, owner_user, idempotency_key)`` 唯一约束是幂等执行的基础。
- ``device_workflow_bindings``：设备与业务工作流的唯一绑定（每设备每业务域一条）。
"""
from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.models import Base


class Workflow(OwnedRecord, Base):
    """工作流定义（可变草稿 + 当前发布修订指针）。

    生命周期：draft -> published -> disabled -> deleted（软删除）。
    关键不变式：``graph`` 始终是"可编辑的最新草稿"，正式运行必须通过 ``published_revision``
    取修订快照；``version`` 作为乐观锁版本，任何保存/发布/停用/删除都会递增。
    """

    __tablename__ = "workflow_definitions"
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="工作流名称")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="用途与团队说明")
    domains: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="共享业务域：分析、预警、质检等")
    graph: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="当前草稿的完整节点、位置、连线及配置")
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, comment="draft/published/disabled/deleted")
    published_revision: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="当前正式运行的不可变修订号")


class WorkflowBusinessState(OwnedRecord, Base):
    """Durable scan positions and notice fingerprints; never modifies source rows."""
    __tablename__ = "workflow_business_states"
    __table_args__ = (UniqueConstraint("workspace_id", "workflow_id", "state_key", name="uq_workflow_business_state"),)
    workflow_id: Mapped[str] = mapped_column(String(26), ForeignKey("workflow_definitions.id"), nullable=False, index=True)
    state_key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class DeviceWorkflowBinding(OwnedRecord, Base):
    """One stable workflow entry for a physical device in each business domain."""

    __tablename__ = "device_workflow_bindings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "device_id", "domain", name="uq_device_workflow_domain"),
        UniqueConstraint("workflow_id", name="uq_device_workflow_definition"),
    )
    device_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("equipment_devices.id"), nullable=False, index=True, comment="共享设备标识",
    )
    domain: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="业务入口：alert（巡检）/alert_maintenance/alert_repair/quality",
    )
    workflow_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("workflow_definitions.id"), nullable=False, index=True, comment="设备在该业务中的唯一工作流",
    )
    provider: Mapped[str] = mapped_column(
        String(20), default="platform", nullable=False, comment="编辑器提供方：platform/dify",
    )
    provider_app_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="外部工作流应用标识；未接入外部编辑器时为空",
    )


class WorkflowRevision(OwnedRecord, Base):
    """不可变修订快照：每保存/发布/恢复一次追加一行，永不改写。

    ``reason`` 是给人看的原因（创建、编辑草稿、发布版本、恢复修订 N、移除等）；
    ``revision`` 与 Workflow.version 同步，``(workflow_id, revision)`` 唯一。
    注意：读取历史修订时仍需按当前权限重新校验图中资源，被撤销权限的快照不可读。
    """

    __tablename__ = "workflow_revisions"
    __table_args__ = (UniqueConstraint("workflow_id", "revision", name="uq_workflow_revision"),)
    workflow_id: Mapped[str] = mapped_column(String(26), ForeignKey("workflow_definitions.id"), nullable=False, index=True, comment="所属流程")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="草稿保存产生的不可变修订号")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="名称、用途、业务域、完整图及资源引用")
    reason: Mapped[str] = mapped_column(String(300), default="保存", nullable=False, comment="创建、保存、恢复等原因")


class WorkflowExecution(OwnedRecord, Base):
    """一次真实运行：图快照 + 真实输入 + 逐节点结果 + 乐观锁控制版本。

    状态机：running -> completed / failed / cancelled / waiting；运行中收到取消请求先进入
    ``cancel_requested``，由执行器在节点边界收尾成 cancelled。``version`` 同时用于
    版本化保存（``save_workflow``/``ExecutionControlInput``）与执行器写入节点状态的 CAS。

    重要约定：``result_json`` 是执行期唯一的节点状态载体（含 nodes/outputs/controls/
    collectionWindow/waiting），接口读取与报告投影都从它取值；``error_message`` 必须已脱敏，
    不得写入驱动原文或模型原始输出。
    """

    __tablename__ = "workflow_executions"
    __table_args__ = (UniqueConstraint("workspace_id", "owner_user_id", "idempotency_key", name="uq_workflow_execution_key"),)
    workflow_id: Mapped[str] = mapped_column(String(26), ForeignKey("workflow_definitions.id"), nullable=False, index=True, comment="执行的流程")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="执行的不可变图修订")
    run_id: Mapped[str] = mapped_column(String(26), ForeignKey("runs.id"), nullable=False, index=True, comment="统一运行中心标识")
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, comment="当前用户的本次执行唯一键，重复提交不重跑")
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="流程、版本和输入摘要，相同键不同输入冲突")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="实际执行的完整图快照")
    input_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="开始节点的实际输入")
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="实际节点状态、限量输出与最终结果")
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False, comment="running/completed/failed/cancelled")
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="脱敏运行错误")
