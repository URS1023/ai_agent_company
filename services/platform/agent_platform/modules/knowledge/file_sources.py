"""知识文档与原件之间的授权和来源校验，供检索与历史证据共用。"""

from __future__ import annotations

from sqlalchemy import exists, or_, select, true

from agent_platform.core.errors import DomainError
from agent_platform.core.files import FileService
from agent_platform.core.models import FileEntry, ResourceReference
from agent_platform.core.settings import Settings

from .models import KnowledgeDocument, KnowledgeSpace

PROTECTED_REFERENCE = 'kb_document_upload'
DOCUMENT_REFERENCE = 'kb_document'


def visible_document_condition(session, actor, *, settings=None, write=False):
    """在分页/计数/检索前过滤原件失权或版本漂移，不泄露隐藏文档数量。"""
    if not session.scalar(select(KnowledgeDocument.id).where(
            KnowledgeDocument.workspace_id == actor.workspace_id,
            KnowledgeDocument.file_id.is_not(None)).limit(1)):
        return true()
    files = FileService(session, actor, settings or Settings(), knowledge_write=write)
    linked = exists(select(ResourceReference.id).where(
        ResourceReference.workspace_id == actor.workspace_id,
        ResourceReference.source_type.in_([PROTECTED_REFERENCE, DOCUMENT_REFERENCE]),
        ResourceReference.source_id == KnowledgeDocument.id,
        ResourceReference.target_type == 'file', ResourceReference.target_id == KnowledgeDocument.file_id))
    accessible = exists(select(FileEntry.id).where(
        *files.conditions(), FileEntry.id == KnowledgeDocument.file_id,
        FileEntry.sha256 == KnowledgeDocument.source_sha256,
        FileEntry.revision == KnowledgeDocument.source_revision))
    return or_(KnowledgeDocument.file_id.is_(None), accessible & linked)


def authorize_document_file(session, actor, document, *, settings=None, write=False):
    """先核文档空间和当前原件ACL，再核固定来源版本；不通过file_id公开原件。"""
    space = session.get(KnowledgeSpace, document.space_id)
    if (document.workspace_id != actor.workspace_id or space is None
            or space.workspace_id != actor.workspace_id):
        raise DomainError(404, 'document_not_found', '文档不存在或已无访问权限')
    if document.file_id is None:
        return None
    actor.require('m08.write' if write else 'm08.read')
    files = FileService(session, actor, settings or Settings(), knowledge_write=write)
    row = files._get(document.file_id)
    if (row.sha256 != document.source_sha256 or row.revision != document.source_revision):
        raise DomainError(403, 'knowledge_source_changed', '原文件版本已变化，请重新导入')
    linked = session.scalar(select(ResourceReference.id).where(
        ResourceReference.workspace_id == actor.workspace_id,
        ResourceReference.source_type.in_([PROTECTED_REFERENCE, DOCUMENT_REFERENCE]),
        ResourceReference.source_id == document.id,
        ResourceReference.target_type == 'file', ResourceReference.target_id == row.id))
    if not linked:
        raise DomainError(403, 'knowledge_source_unbound', '原文件关联已失效')
    return row


def source_file_dict(row):
    return {key: getattr(row, key) for key in ('id', 'name', 'content_type', 'size', 'sha256', 'revision')}


def remove_document_references(session, actor, document):
    """仅解除本知识文档的引用；最后一份知识上传解除前先转为原所有者私有。"""
    if document.file_id is None:
        return
    references = list(session.scalars(select(ResourceReference).where(
        ResourceReference.workspace_id == actor.workspace_id,
        ResourceReference.source_type.in_([PROTECTED_REFERENCE, DOCUMENT_REFERENCE]),
        ResourceReference.source_id == document.id,
        ResourceReference.target_type == 'file', ResourceReference.target_id == document.file_id)))
    if any(row.source_type == PROTECTED_REFERENCE for row in references):
        other = session.scalar(select(ResourceReference.id).where(
            ResourceReference.workspace_id == actor.workspace_id,
            ResourceReference.source_type == PROTECTED_REFERENCE,
            ResourceReference.target_type == 'file', ResourceReference.target_id == document.file_id,
            ResourceReference.source_id != document.id))
        if not other:
            file = session.get(FileEntry, document.file_id)
            if file and file.workspace_id == actor.workspace_id:
                file.visibility = 'private'
                file.revision += 1
    for reference in references:
        session.delete(reference)
