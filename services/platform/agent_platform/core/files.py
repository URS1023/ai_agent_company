"""授权文件存储；磁盘路径由服务生成，客户端只能传文件ID。"""

import hashlib
from pathlib import Path

from sqlalchemy import or_, select

from .context import Actor
from .errors import DomainError
from .models import FileEntry, ResourceGrant, ResourceReference, new_id
from .records import RecordStore
from .settings import Settings

TEXT_SUFFIXES = {'.txt', '.md', '.csv', '.json', '.log'}
UPLOAD_SUFFIXES = TEXT_SUFFIXES | {'.pdf', '.docx', '.xlsx', '.pptx', '.png', '.jpg', '.jpeg', '.webp'}


def file_dict(row: FileEntry):
    return {key: getattr(row, key) for key in ('id', 'workspace_id', 'owner_id', 'visibility', 'name',
            'content_type', 'size', 'sha256', 'status', 'revision', 'created_at')}


class FileService:
    def __init__(self, session, actor: Actor, settings: Settings):
        self.session, self.actor, self.settings = session, actor, settings

    def conditions(self):
        rules = [FileEntry.workspace_id == self.actor.workspace_id, FileEntry.deleted.is_(False)]
        if not self.actor.is_admin:
            grants = self.session.scalars(select(ResourceGrant).where(ResourceGrant.workspace_id == self.actor.workspace_id,
                    ResourceGrant.user_id == self.actor.user_id, ResourceGrant.resource_type == 'file'))
            granted_ids = [g.resource_id for g in grants if 'read' in g.actions or '*' in g.actions]
            rules.append(or_(FileEntry.visibility == 'workspace', FileEntry.owner_id == self.actor.user_id,
                             FileEntry.id.in_(granted_ids)))
        return rules

    def _get(self, id: str) -> FileEntry:
        row = self.session.scalar(select(FileEntry).where(*self.conditions(), FileEntry.id == id))
        if row is None:
            raise DomainError(404, 'file_not_found', '文件不存在或已无访问权限')
        return row

    def get(self, id: str) -> dict:
        return file_dict(self._get(id))

    def path(self, id: str) -> Path:
        row = self._get(id)
        root = self.settings.files_dir.resolve()
        path = (root / row.storage_key).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise DomainError(404, 'file_content_missing', '文件内容暂不可用，请联系管理员')
        return path

    def read_text(self, id: str) -> str:
        row = self._get(id)
        if Path(row.name).suffix.lower() not in TEXT_SUFFIXES or row.size > 2 * 1024 * 1024:
            raise DomainError(422, 'file_text_not_supported', '请选择2MB以内的文本文件')
        try:
            return self.path(id).read_text(encoding='utf-8-sig')
        except UnicodeError as exc:
            raise DomainError(422, 'file_encoding_invalid', '文件需要使用UTF-8编码') from exc

    def store_bytes(self, name: str, content: bytes, content_type: str,
                    source: dict | None = None, visibility='private') -> dict:
        name = Path(name.replace('\\', '/')).name
        if not name or len(name) > 240 or not content or len(content) > self.settings.max_upload_bytes:
            raise DomainError(422, 'invalid_file', '文件为空、名称无效或超过大小限制')
        if visibility not in {'private', 'workspace'}:
            raise DomainError(422, 'invalid_visibility', '请选择有效的文件可见范围')
        id = new_id()
        key = self.actor.workspace_id + '/' + id
        path = self.settings.files_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        # 文件名不参与磁盘路径，排他写防止覆盖其他版本或目录穿越。
        with path.open('xb') as f:
            f.write(content)
        row = FileEntry(id=id, workspace_id=self.actor.workspace_id, owner_id=self.actor.user_id,
                        visibility=visibility, name=name, content_type=content_type, size=len(content),
                        sha256=hashlib.sha256(content).hexdigest(), storage_key=key)
        self.session.add(row)
        self.session.flush()
        RecordStore(self.session, self.actor).audit('file.create', 'file', row.id, {'name': name, 'size': row.size})
        if source:
            self.session.add(ResourceReference(workspace_id=self.actor.workspace_id,
                source_type=str(source['type']), source_id=str(source['id']), target_type='file', target_id=id))
            self.session.flush()
        return file_dict(row)

    def references(self, id: str) -> list[dict]:
        self._get(id)
        rows = self.session.scalars(select(ResourceReference).where(
            ResourceReference.workspace_id == self.actor.workspace_id,
            ResourceReference.target_type == 'file', ResourceReference.target_id == id))
        return [{'type': r.source_type, 'id': r.source_id} for r in rows]
