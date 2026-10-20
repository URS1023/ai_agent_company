"""知识结果的不可变来源与当前授权复核；不重新检索或调用向量模型。"""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError

from .models import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from .file_sources import authorize_document_file


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def knowledge_evidence(actor: Actor, space_ids: list[str]) -> dict:
    """即使没有命中也记录查询空间，避免空结果历史绕过知识读权限。"""
    return {'schema_version': 1, 'workspace_id': actor.workspace_id,
        'space_ids': list(dict.fromkeys(space_ids)), 'chunks': []}


def merge_evidence(current: dict, incoming: dict) -> dict:
    """以新对象合并来源，保证SQLAlchemy JSON能识别步骤增量，不覆盖先前知识来源。"""
    if (current.get('schema_version') != 1 or incoming.get('schema_version') != 1
            or current.get('workspace_id') != incoming.get('workspace_id')):
        raise DomainError(500, 'knowledge_evidence_invalid', '知识来源记录不一致')
    chunks = {item['chunk_id']: item for item in current['chunks']}
    for item in incoming['chunks']:
        previous = chunks.get(item['chunk_id'])
        if previous is not None and previous != item:
            raise DomainError(409, 'knowledge_source_changed', '运行期间知识内容已变化，请重新发起新运行')
        chunks[item['chunk_id']] = item
    return {**current, 'space_ids': list(dict.fromkeys(current['space_ids'] + incoming['space_ids'])),
        'chunks': list(chunks.values())}


def authorize_evidence(session: Session, actor: Actor, snapshot: dict | None, *, legacy_steps=None) -> None:
    """历史只核被冻结的空间/文档/片段；撤权、删除或来源漂移后拒绝，不查询最新流程绑定。"""
    denied = lambda: DomainError(403, 'knowledge_evidence_access_denied', '当前账号无法访问该结果引用的知识')
    if snapshot is None:
        # 旧纯文本/模型运行不依赖知识；旧知识运行缺失来源时不能凭当前定义重建证据。
        if (not isinstance(legacy_steps, list) or not legacy_steps
                or any(not isinstance(item, dict) or item.get('type') not in {'text', 'ai'} for item in legacy_steps)):
            raise denied()
        return
    if (not isinstance(snapshot, dict) or snapshot.get('schema_version') != 1
            or snapshot.get('workspace_id') != actor.workspace_id
            or not isinstance(snapshot.get('space_ids'), list) or not isinstance(snapshot.get('chunks'), list)):
        raise denied()
    spaces = snapshot['space_ids']
    if not all(isinstance(item, str) and item for item in spaces):
        raise denied()
    if spaces:
        actor.require('m08.read')
    for space_id in set(spaces):
        space = session.get(KnowledgeSpace, space_id)
        if space is None or space.workspace_id != actor.workspace_id:
            raise denied()
    for source in snapshot['chunks']:
        if (not isinstance(source, dict) or source.get('space_id') not in spaces
                or not all(isinstance(source.get(key), str) and source[key]
                    for key in ('chunk_id', 'document_id', 'content_sha256'))):
            raise denied()
        chunk = session.get(KnowledgeChunk, source['chunk_id'])
        document = session.get(KnowledgeDocument, source['document_id'])
        if (chunk is None or document is None or chunk.workspace_id != actor.workspace_id
                or document.workspace_id != actor.workspace_id or chunk.space_id != source['space_id']
                or document.space_id != source['space_id'] or chunk.document_id != document.id
                or content_sha256(chunk.content) != source['content_sha256']):
            raise denied()
        try:
            authorize_document_file(session, actor, document)
        except DomainError as exc:
            raise denied() from exc
        if document.file_id is not None and (
                source.get('file_id') != document.file_id
                or source.get('source_sha256') != document.source_sha256
                or source.get('source_revision') != document.source_revision):
            raise denied()
