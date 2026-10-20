"""配置及资源目录的工作区仓储；领域实体不依赖动态字典代替业务模型。"""

from copy import deepcopy
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from .context import Actor
from .errors import DomainError
from .models import AuditEvent, ResourceGrant, ResourceRecord, new_id, now_iso

PROTECTED = {'id', 'workspace_id', 'revision', 'created_at', 'updated_at', 'owner_id', 'deleted'}
SECRET_KEYS = {'password', 'password_hash', 'token', 'api_key', 'authorization', 'secret', 'encrypted_value', 'value'}


def sanitized(value: Any, *, preserve_business_value: bool = False) -> Any:
    """审计默认隐藏通用value；任务结果可保留业务值，显式凭据键仍隐藏。"""
    if isinstance(value, dict):
        hidden = SECRET_KEYS - {'value'} if preserve_business_value else SECRET_KEYS
        return {k: ('[已隐藏]' if k.lower() in hidden else
                    sanitized(v, preserve_business_value=preserve_business_value)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitized(v, preserve_business_value=preserve_business_value) for v in value]
    return value


def record_dict(row: ResourceRecord) -> dict:
    return {**deepcopy(row.data), 'id': row.id, 'name': row.name, 'workspace_id': row.workspace_id,
            'revision': row.revision, 'owner_id': row.owner_id, 'visibility': row.visibility,
            'archived': row.archived, 'created_at': row.created_at, 'updated_at': row.updated_at}


class RecordStore:
    def __init__(self, session: Session, actor: Actor):
        self.session = session
        self.actor = actor

    def _scope(self, kind: str):
        conditions = [ResourceRecord.workspace_id == self.actor.workspace_id,
                      ResourceRecord.kind == kind, ResourceRecord.deleted.is_(False)]
        if not self.actor.is_admin:
            grants = self.session.scalars(select(ResourceGrant).where(
                ResourceGrant.workspace_id == self.actor.workspace_id,
                ResourceGrant.user_id == self.actor.user_id, ResourceGrant.resource_type == kind))
            granted_ids = [g.resource_id for g in grants if 'read' in g.actions or '*' in g.actions]
            conditions.append(or_(ResourceRecord.visibility == 'workspace',
                                  ResourceRecord.owner_id == self.actor.user_id, ResourceRecord.id.in_(granted_ids)))
        return conditions

    def list(self, kind: str, q: str = '', limit: int = 50, offset: int = 0, include_archived: bool = False) -> dict:
        conditions = self._scope(kind)
        if not include_archived:
            conditions.append(ResourceRecord.archived.is_(False))
        if q:
            conditions.append(ResourceRecord.name.contains(q, autoescape=True))
        total = self.session.scalar(select(func.count()).select_from(ResourceRecord).where(*conditions)) or 0
        rows = self.session.scalars(select(ResourceRecord).where(*conditions)
                                    .order_by(ResourceRecord.updated_at.desc(), ResourceRecord.id)
                                    .limit(min(max(limit, 1), 500)).offset(max(offset, 0)))
        return {'items': [record_dict(r) for r in rows], 'total': total}

    def _get(self, kind: str, id: str) -> ResourceRecord:
        row = self.session.scalar(select(ResourceRecord).where(*self._scope(kind), ResourceRecord.id == id))
        if row is None:
            raise DomainError(404, 'resource_not_found', '资源不存在或已无访问权限')
        return row

    def get(self, kind: str, id: str) -> dict:
        return record_dict(self._get(kind, id))

    def assert_action(self, kind: str, id: str, action: str) -> ResourceRecord:
        row = self._get(kind, id)
        if row.visibility == 'private' and row.owner_id != self.actor.user_id and not self.actor.is_admin:
            grants = self.session.scalars(select(ResourceGrant).where(
                ResourceGrant.workspace_id == self.actor.workspace_id, ResourceGrant.user_id == self.actor.user_id,
                ResourceGrant.resource_type == kind, ResourceGrant.resource_id == id))
            if not any(action in grant.actions or '*' in grant.actions for grant in grants):
                raise DomainError(403, 'resource_read_only', '当前资源授权不允许此操作')
        return row

    def create(self, kind: str, data: dict, *, id: str | None = None, visibility: str = 'workspace') -> dict:
        if visibility not in {'workspace', 'private'}:
            raise DomainError(422, 'invalid_visibility', '请选择有效的可见范围')
        clean = {k: deepcopy(v) for k, v in data.items() if k not in PROTECTED}
        row = ResourceRecord(id=id or new_id(), workspace_id=self.actor.workspace_id, kind=kind,
                             name=str(clean.get('name', clean.get('title', ''))).strip(), data=clean,
                             owner_id=self.actor.user_id, visibility=visibility)
        self.session.add(row)
        self.session.flush()
        self.audit('create', kind, row.id, {'name': row.name})
        return record_dict(row)

    def update(self, kind: str, id: str, changes: dict, expected_revision: int) -> dict:
        row = self.assert_action(kind, id, 'write')
        if 'visibility' in changes:
            if changes['visibility'] not in {'private', 'workspace'}:
                raise DomainError(422, 'invalid_visibility', '请选择有效的可见范围')
            if changes['visibility'] != row.visibility and not self.actor.is_admin and row.owner_id != self.actor.user_id:
                raise DomainError(403, 'owner_required', '只有所有者或管理员可更改可见范围')
        if row.revision != expected_revision:
            raise DomainError(409, 'revision_conflict', '内容已被更新，请刷新后再编辑')
        data = {**deepcopy(row.data), **{k: deepcopy(v) for k, v in changes.items() if k not in PROTECTED}}
        result = self.session.execute(update(ResourceRecord).where(
            ResourceRecord.id == row.id, ResourceRecord.workspace_id == self.actor.workspace_id,
            ResourceRecord.revision == expected_revision).values(
                data=data, name=str(data.get('name', data.get('title', row.name))).strip(),
                visibility=changes.get('visibility', row.visibility),
                archived=bool(changes.get('archived', row.archived)),
                revision=expected_revision + 1, updated_at=now_iso()),
            execution_options={'synchronize_session': False})
        if result.rowcount != 1:
            raise DomainError(409, 'revision_conflict', '内容已被更新，请刷新后再编辑')
        self.session.expire(row)
        self.audit('update', kind, id, {'fields': sorted(k for k in changes if k not in PROTECTED)})
        return record_dict(row)

    def delete(self, kind: str, id: str, expected_revision: int):
        row = self.assert_action(kind, id, 'delete')
        if row.revision != expected_revision:
            raise DomainError(409, 'revision_conflict', '内容已被更新，请刷新后重试')
        result = self.session.execute(update(ResourceRecord).where(ResourceRecord.id == id,
            ResourceRecord.workspace_id == self.actor.workspace_id, ResourceRecord.revision == expected_revision)
            .values(deleted=True, revision=expected_revision + 1, updated_at=now_iso()),
            execution_options={'synchronize_session': False})
        if result.rowcount != 1:
            raise DomainError(409, 'revision_conflict', '内容已被更新，请刷新后重试')
        self.session.expire(row)
        self.audit('delete', kind, id)
        self.session.flush()
        return {'id': id, 'deleted': True, 'revision': row.revision}

    def audit(self, action: str, resource_type: str, resource_id: str, details: dict | None = None):
        event = AuditEvent(workspace_id=self.actor.workspace_id, user_id=self.actor.user_id,
                           action=action, resource_type=resource_type, resource_id=resource_id,
                           details=sanitized(details or {}))
        self.session.add(event)
        self.session.flush()
        return event.id
