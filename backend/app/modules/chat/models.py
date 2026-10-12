# 对话业务模块选择的独立持久化表（原 docstring 见下）。
# 会话主表 `conversations` 已有线上数据，因此业务模块归属放在本模块的附属表里，
# 避免给主表加列导致历史行不兼容。
# 只在 `chat/service.create_conversation` 写入，读取方是会话列表与会话详情接口
# （`chat/router.list_conversations`、`conversation_detail`），用于回显业务助手类型。
# 约束：`conversation_id` 既是主键又是外键，一个会话最多一个业务模块；
# 模块取值必须来自 `chat/business_modules.BUSINESS_MODULES`，写入前由 service 校验。
"""Persist business context separately so existing conversation rows stay compatible."""
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ...models import Base


class ConversationBusinessContext(Base):
    """会话 → 业务模块（设备/质检/财务/销售/采购/能耗/仓储）的归属映射。

    生命周期与会话一致；删除会话只归档 `conversations.archived_at`，本表不做级联删除，
    因此读取方必须按会话 id 取用，不能把残留行当成"仍在使用"。
    不变式：`module_id` 一定属于 `BUSINESS_MODULES` 的键（写入前经 `module_instructions` 校验）。
    """

    __tablename__ = "conversation_business_contexts"
    conversation_id: Mapped[str] = mapped_column(String(26), ForeignKey("conversations.id"), primary_key=True)
    # 与 BusinessModuleId 字面量集合保持一致，长度 32 足够覆盖现有模块 id。
    module_id: Mapped[str] = mapped_column(String(32), nullable=False)
