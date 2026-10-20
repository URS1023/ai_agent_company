"""个人记忆的手工管理，不采集对话，也不调用模型或向量服务。

来源为本地锁定 Open WebUI backend/open_webui/models/memories.py 的
insert_new_memory、update_memory_by_id_and_user_id、delete_memory_by_id_and_user_id，
以及 utils/memory.py 的 clean_memory_content 和字面查询语义。
适配平台同步事务，补齐工作区、修订条件更新、数据库分页和不含正文的审计。
上游许可见 licenses/OpenWebUI-LICENSE.txt 及 OpenWebUI-LICENSE_NOTICE.txt。
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, select, update

from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import now_iso
from agent_platform.core.records import RecordStore
from .memory_models import UserMemory


MemoryType = Literal["user", "context"]


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=6000)
    type: MemoryType = "user"

    @field_validator("content")
    @classmethod
    def clean_content(cls, value):
        """复用上游去首尾空白口径，拒绝空白正文和不可保存的空字符。"""
        value = value.strip()
        if not value or "\0" in value:
            raise ValueError("请输入有效的记忆内容")
        return value


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=6000)
    type: MemoryType | None = None

    @field_validator("content")
    @classmethod
    def clean_content(cls, value):
        return MemoryCreate.clean_content(value) if value is not None else value

    @model_validator(mode="after")
    def nonempty_changes(self):
        if not self.model_fields_set & {"content", "type"}:
            raise ValueError("请修改记忆内容或类型")
        if any(getattr(self, key) is None for key in self.model_fields_set & {"content", "type"}):
            raise ValueError("内容和类型不能为空")
        return self


def memory_view(row: UserMemory) -> dict:
    return {key: getattr(row, key) for key in ("id", "workspace_id", "user_id", "content", "type",
                                             "revision", "created_at", "updated_at")}


class MemoryService:
    """所有读取和写入均约束当前用户及工作区，管理员也不能越过所属关系。"""

    def __init__(self, session, actor: Actor):
        self.session, self.actor = session, actor

    def _scope(self):
        return (UserMemory.workspace_id == self.actor.workspace_id, UserMemory.user_id == self.actor.user_id)

    def _get(self, identifier):
        row = self.session.scalar(select(UserMemory).where(*self._scope(), UserMemory.id == identifier))
        if row is None:
            raise DomainError(404, "memory_not_found", "记忆不存在或已删除")
        return row

    def list(self, q="", *, type: MemoryType | None = None, limit=50, offset=0):
        self.actor.require("m02.read")
        if not isinstance(q, str) or len(q) > 200 or not 1 <= limit <= 100 or offset < 0 or type not in (None, "user", "context"):
            raise DomainError(422, "memory_filter_invalid", "请检查搜索条件和页码")
        conditions = list(self._scope())
        if q.strip():
            # 保留上游不区分大小写的字面子串搜索，百分号和下划线不作为通配符。
            conditions.append(UserMemory.content.icontains(q.strip(), autoescape=True))
        if type:
            conditions.append(UserMemory.type == type)
        total = self.session.scalar(select(func.count()).select_from(UserMemory).where(*conditions)) or 0
        rows = self.session.scalars(select(UserMemory).where(*conditions)
            .order_by(UserMemory.updated_at.desc(), UserMemory.id).limit(limit).offset(offset))
        return {"items": [memory_view(row) for row in rows], "total": total}

    def get(self, identifier):
        self.actor.require("m02.read")
        return memory_view(self._get(identifier))

    def create(self, data: MemoryCreate):
        self.actor.require("m02.write")
        row = UserMemory(workspace_id=self.actor.workspace_id, user_id=self.actor.user_id,
                         content=data.content, type=data.type)
        self.session.add(row)
        self.session.flush()
        self._audit("create", row.id, {"type": row.type})
        return memory_view(row)

    def update(self, identifier, data: MemoryPatch):
        self.actor.require("m02.write")
        row = self._get(identifier)
        changes = data.model_dump(exclude_unset=True, exclude={"revision"})
        result = self.session.execute(update(UserMemory).where(*self._scope(), UserMemory.id == identifier,
            UserMemory.revision == data.revision).values(**changes, revision=data.revision + 1, updated_at=now_iso()),
            execution_options={"synchronize_session": False})
        if result.rowcount != 1:
            raise DomainError(409, "revision_conflict", "记忆已更新，请刷新后重试")
        self.session.expire(row)
        self._audit("update", identifier, {"fields": sorted(changes)})
        return memory_view(row)

    def delete(self, identifier, revision):
        self.actor.require("m02.delete")
        self._get(identifier)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise DomainError(422, "memory_revision_invalid", "请刷新后重试")
        result = self.session.execute(delete(UserMemory).where(*self._scope(), UserMemory.id == identifier,
            UserMemory.revision == revision), execution_options={"synchronize_session": False})
        if result.rowcount != 1:
            raise DomainError(409, "revision_conflict", "记忆已更新，请刷新后重试")
        self._audit("delete", identifier)
        # 正文真实删除；不在审计、资源目录或检索缓存中保留正文副本。
        self.session.expire_all()
        return {"id": identifier, "deleted": True}

    def _audit(self, action, identifier, details=None):
        RecordStore(self.session, self.actor).audit(action, "user_memory", identifier, details)
