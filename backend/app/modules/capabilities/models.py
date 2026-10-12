"""Workspace-scoped catalog metadata for registered capability methods.

工具与能力中心的"治理元数据"表定义：分类树 + 工具方法的启停/分类/展示信息。

在系统中的位置：工具本身仍由内置注册表（builtin_provider）或不可变的连接器版本
提供，本模块的两张表**只承载治理与展示**，不复制工具定义、不存储可执行逻辑。
因此删除这里的记录不会让工具消失，只是回到"未分类/默认启用"状态。

关键约束：
- 两张表都按 workspace_id 唯一：分类 code 与同级名称唯一、方法 tool_name 与 code 唯一，
  保证跨工作区互不可见。
- ``ToolMethodMetadata.enabled`` 是 AI/工作流是否还能发起新调用的判定依据
  （ToolGateway 直接读该列，不走 ORM 缓存），改成 False 即"停用即从模型工具列表移除"。
- 本模块不含路由，只被 capabilities/catalog_service 与 provider 层读取。
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.modules.system.models import WorkspaceRecord

__all__ = ["ToolCategory", "ToolMethodMetadata"]


class ToolCategory(WorkspaceRecord, Base):
    """A compact category tree shared by builtin and connector-backed methods.

    中文补充：工具分类树的一个节点（同一棵树同时用于内置工具与连接器工具）。
    ``parent_id`` 自引用形成层级；``code`` 供导出/引用使用，``name`` 是同级唯一的
    显示名；``status`` 为 active 之外的取值表示已归档（归档前要求子类与已归类
    方法为空，见 catalog_service.archive_category）。
    不变式：分类只能组织"已登记的真实工具"，不允许创建无工具的空目录来伪装能力。
    """

    __tablename__ = "capability_tool_categories"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_capability_category_code"),
        UniqueConstraint("workspace_id", "parent_id", "name", name="uq_capability_category_sibling"),
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("capability_tool_categories.id"), nullable=True, index=True,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    updated_by_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)


class ToolMethodMetadata(WorkspaceRecord, Base):
    """Governance overlay for a real registered tool method.

    Tools continue to be supplied by the builtin registry or immutable connector
    versions. This row only governs display metadata, classification and whether
    the method is eligible for new AI/workflow execution.

    中文补充：只承载治理元数据（分类、展示名、标签、启停），不复制工具定义；
    未治理过的工具没有本行，按"默认启用 + 名称即展示名"处理，
    因此读到 None 不能当成"工具不存在"。
    """

    # 注意：本表的 enabled 是"新调用的准入门"，不回溯中断已在进行中的运行；
    # ToolGateway 会为每次调用实时读取该列，因此停用后下一个工具调用即被拒绝。

    __tablename__ = "capability_tool_method_metadata"
    __table_args__ = (
        UniqueConstraint("workspace_id", "tool_name", name="uq_capability_method_tool"),
        UniqueConstraint("workspace_id", "code", name="uq_capability_method_code"),
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("capability_tool_categories.id"), nullable=True, index=True,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    updated_by_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
