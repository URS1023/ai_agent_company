"""知识库业务：切块、向量化、入库与检索。全部在平台内完成，不依赖外部知识系统。"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.embeddings import EmbeddingClient, cosine
from agent_platform.core.datetimes import utc_storage_iso
from agent_platform.core.errors import DomainError
from agent_platform.core.settings import Settings

from .models import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from .evidence import content_sha256, knowledge_evidence
from .file_sources import (authorize_document_file, remove_document_references,
                           source_file_dict, visible_document_condition)

MAX_DOCUMENT_CHARS = 200_000
CHUNK_SIZE = 600
CHUNK_OVERLAP = 80


def split_chunks(text: str) -> list[str]:
    """按段落聚合成块，过长段落再按句末切分，块间保留少量重叠以免切断语义。"""
    text = re.sub(r'\r\n?', '\n', text).strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    chunks: list[str] = []
    buffer = ''
    for paragraph in paragraphs:
        if len(buffer) + len(paragraph) + 2 <= CHUNK_SIZE:
            buffer = f'{buffer}\n\n{paragraph}'.strip()
            continue
        if buffer:
            chunks.append(buffer)
        while len(paragraph) > CHUNK_SIZE:
            cut = paragraph.rfind('。', 0, CHUNK_SIZE)
            if cut < CHUNK_SIZE // 2:
                cut = paragraph.rfind('\n', 0, CHUNK_SIZE)
            if cut < CHUNK_SIZE // 2:
                cut = CHUNK_SIZE
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[max(0, cut - CHUNK_OVERLAP):].strip()
        buffer = paragraph
    if buffer:
        chunks.append(buffer)
    return [c for c in chunks if c]


class KnowledgeService:
    def __init__(self, session: Session, actor: Actor, embeddings: EmbeddingClient | None = None,
                 settings=None) -> None:
        self.session = session
        self.actor = actor
        self.embeddings = embeddings or EmbeddingClient()
        self.settings = settings

    # ---- 知识空间 ----
    def spaces(self) -> dict:
        rows = self.session.scalars(
            select(KnowledgeSpace).where(KnowledgeSpace.workspace_id == self.actor.workspace_id)
            .order_by(KnowledgeSpace.created_at.desc())).all()
        counts = {row.id: 0 for row in rows}
        if rows:
            for document in self.session.scalars(
                    select(KnowledgeDocument).where(KnowledgeDocument.workspace_id == self.actor.workspace_id,
                        KnowledgeDocument.space_id.in_(list(counts)),
                        visible_document_condition(self.session, self.actor, settings=self.settings))):
                counts[document.space_id] = counts.get(document.space_id, 0) + 1
        items = [{'id': r.id, 'name': r.name, 'description': r.description,
                  'document_count': counts.get(r.id, 0),
                  'created_at': utc_storage_iso(r.created_at)} for r in rows]
        return {'items': items, 'total': len(items)}

    def create_space(self, name: str, description: str = '') -> dict:
        name = name.strip()
        if not name:
            raise DomainError(422, 'space_name_required', '请填写知识空间名称', {'name': '必填'})
        exists = self.session.scalars(select(KnowledgeSpace).where(
            KnowledgeSpace.workspace_id == self.actor.workspace_id, KnowledgeSpace.name == name)).first()
        if exists:
            raise DomainError(409, 'space_name_exists', '已存在同名的知识空间', {'name': '名称重复'})
        space = KnowledgeSpace(workspace_id=self.actor.workspace_id, name=name,
                               description=description.strip(), created_by=self.actor.user_id)
        self.session.add(space)
        self.session.flush()
        return {'id': space.id, 'name': space.name, 'description': space.description,
                'document_count': 0, 'created_at': utc_storage_iso(space.created_at)}

    def space(self, space_id: str) -> KnowledgeSpace:
        space = self.session.get(KnowledgeSpace, space_id)
        if not space or space.workspace_id != self.actor.workspace_id:
            raise DomainError(404, 'space_not_found', '知识空间不存在或已删除')
        return space

    def authorize_spaces(self, space_ids: list[str]) -> None:
        """供Agent和流程绑定/执行共用；业务服务内再次鉴权，不能依赖知识路由已经检查。"""
        if space_ids:
            self.actor.require('m08.read')
        for space_id in dict.fromkeys(space_ids):
            self.space(space_id)

    def delete_space(self, space_id: str) -> dict:
        space = self.space(space_id)
        for chunk in self.session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.space_id == space_id)):
            self.session.delete(chunk)
        for document in self.session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.space_id == space_id)):
            remove_document_references(self.session, self.actor, document)
            self.session.delete(document)
        self.session.delete(space)
        return {'deleted': True, 'id': space_id}

    # ---- 文档 ----
    def documents(self, space_id: str, q: str = '', limit: int = 50, offset: int = 0) -> dict:
        self.space(space_id)
        conditions = [KnowledgeDocument.workspace_id == self.actor.workspace_id,
            KnowledgeDocument.space_id == space_id,
            visible_document_condition(self.session, self.actor, settings=self.settings)]
        if q.strip():
            conditions.append(KnowledgeDocument.name.contains(q.strip(), autoescape=True))
        total = self.session.scalar(select(func.count()).select_from(KnowledgeDocument).where(*conditions)) or 0
        rows = self.session.scalars(select(KnowledgeDocument).where(*conditions)
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id)
            .limit(min(max(limit, 1), 100)).offset(max(offset, 0)))
        return {'items': [self.document_dict(row) for row in rows], 'total': total}

    def document_row(self, document_id: str, *, write=False) -> KnowledgeDocument:
        document = self.session.get(KnowledgeDocument, document_id)
        if document is None or document.workspace_id != self.actor.workspace_id:
            raise DomainError(404, 'document_not_found', '文档不存在或已无访问权限')
        authorize_document_file(self.session, self.actor, document, settings=self.settings, write=write)
        return document

    def document_dict(self, document: KnowledgeDocument, *, write=False) -> dict:
        file = authorize_document_file(self.session, self.actor, document, settings=self.settings, write=write)
        parser = document.parser_metadata or {}
        return {'id': document.id, 'space_id': document.space_id, 'name': document.name,
            'source': document.source, 'char_count': document.char_count, 'chunk_count': document.chunk_count,
            'status': document.status, 'error': document.error, 'error_code': parser.get('error_code'),
            'created_at': utc_storage_iso(document.created_at),
            'source_file': source_file_dict(file) if file else None, 'parser': parser or None}

    def document(self, document_id: str) -> dict:
        self.actor.require('m08.read')
        return self.document_dict(self.document_row(document_id))

    def chunks(self, document_id: str, *, limit=50, offset=0) -> dict:
        self.actor.require('m08.read')
        document = self.document_row(document_id)
        conditions = [KnowledgeChunk.workspace_id == self.actor.workspace_id,
            KnowledgeChunk.document_id == document.id]
        total = self.session.scalar(select(func.count()).select_from(KnowledgeChunk).where(*conditions)) or 0
        rows = self.session.scalars(select(KnowledgeChunk).where(*conditions)
            .order_by(KnowledgeChunk.ordinal, KnowledgeChunk.id).limit(min(max(limit, 1), 100)).offset(max(offset, 0)))
        return {'items': [{'id': row.id, 'ordinal': row.ordinal, 'content': row.content,
            'source_location': row.source_location or {}} for row in rows], 'total': total}

    def import_text(self, space_id: str, name: str, text: str, source: str = 'paste') -> dict:
        """保持旧API字段，复用原件摄取的持久预约、失败记录和明确索引预算。"""
        from .ingestion import KnowledgeIngestion
        return KnowledgeIngestion(self, self.settings or Settings()).import_text(space_id, name, text, source)

    def delete_document(self, document_id: str) -> dict:
        document = self.document_row(document_id, write=self.actor.can('m08.write'))
        for chunk in self.session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)):
            self.session.delete(chunk)
        remove_document_references(self.session, self.actor, document)
        self.session.delete(document)
        return {'deleted': True, 'id': document_id}

    # ---- 检索 ----
    def search(self, space_ids: list[str], query: str, top_k: int = 5) -> dict:
        self.actor.require('m08.read')
        query = query.strip()
        if not query:
            raise DomainError(422, 'query_required', '请输入检索内容', {'query': '必填'})
        self.authorize_spaces(space_ids)
        evidence = knowledge_evidence(self.actor, space_ids)
        visible_documents = select(KnowledgeDocument.id).where(
            KnowledgeDocument.workspace_id == self.actor.workspace_id,
            KnowledgeDocument.space_id.in_(space_ids), KnowledgeDocument.status == 'ready',
            visible_document_condition(self.session, self.actor, settings=self.settings))
        rows = self.session.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.workspace_id == self.actor.workspace_id,
            KnowledgeChunk.space_id.in_(space_ids), KnowledgeChunk.document_id.in_(visible_documents))).all()
        if not rows:
            return {'items': [], 'total': 0, 'knowledge_sources': evidence}
        vector = self.embeddings.embed([query])[0]
        documents = {d.id: d for d in self.session.scalars(
            select(KnowledgeDocument).where(KnowledgeDocument.workspace_id == self.actor.workspace_id,
                KnowledgeDocument.id.in_({row.document_id for row in rows})))}
        scored = sorted(((cosine(vector, chunk.vector or []), chunk) for chunk in rows),
                        key=lambda pair: pair[0], reverse=True)[:top_k]
        items = [{'chunk_id': chunk.id, 'document_id': chunk.document_id, 'space_id': chunk.space_id,
                  'document_name': documents[chunk.document_id].name, 'ordinal': chunk.ordinal,
                  'content': chunk.content, 'score': round(score, 4),
                  'source_location': chunk.source_location or {},
                  'source_file': self.document_dict(documents[chunk.document_id])['source_file']}
                 for score, chunk in scored if score > 0]
        evidence['chunks'] = [{'chunk_id': item['chunk_id'], 'document_id': item['document_id'],
            'space_id': item['space_id'], 'content_sha256': content_sha256(item['content']),
            **({'file_id': item['source_file']['id'], 'source_sha256': item['source_file']['sha256'],
                'source_revision': item['source_file']['revision']} if item['source_file'] else {})} for item in items]
        return {'items': items, 'total': len(items), 'knowledge_sources': evidence}
