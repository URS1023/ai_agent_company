"""知识摄取：文件与粘贴先持久化正文/来源，再有界索引；失败不抹去用户输入。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from agent_platform.core.errors import DomainError
from agent_platform.core.embedding_vectors import validate_vectors
from agent_platform.core.files import FileService
from agent_platform.core.models import ResourceReference

from . import extractor
from .file_sources import (DOCUMENT_REFERENCE, PROTECTED_REFERENCE,
                           authorize_document_file)
from .models import KnowledgeChunk, KnowledgeDocument

MAX_CHUNKS = 500
PARSE_SECONDS = 20
INDEX_SECONDS = 60
TOTAL_SECONDS = 90
EMBEDDING_BATCH = 32


def parse_in_worker(path: Path, name: str, timeout: float) -> dict:
    """受控解析器不继承provider密钥，超时直接终止本次子进程；解析库不派生外部转换器。"""
    import os
    environment = {key: value for key, value in os.environ.items()
        if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH', 'LANG', 'LC_ALL'}}
    try:
        process = subprocess.run([sys.executable, '-I', str(Path(extractor.__file__).resolve())],
            input=json.dumps({'path': str(path), 'name': name}).encode(),
            capture_output=True, timeout=max(0.01, min(timeout, PARSE_SECONDS)), env=environment)
    except subprocess.TimeoutExpired as exc:
        # subprocess.run已kill并wait此唯一子进程，不留下后台解析工作。
        raise DomainError(504, 'document_parse_timeout', '文件解析超时，请拆分文件后重新上传') from exc
    if process.returncode or len(process.stdout) > 4 * 1024 * 1024:
        raise DomainError(422, 'document_parse_failed', '文件解析未完成，请检查文件后重新上传')
    try:
        result = json.loads(process.stdout)
    except (ValueError, UnicodeError) as exc:
        raise DomainError(422, 'document_parse_failed', '文件解析返回内容无效') from exc
    if not isinstance(result, dict):
        raise DomainError(422, 'document_parse_failed', '文件解析返回内容无效')
    if not result.get('ok'):
        raise DomainError(422, result.get('code', 'document_parse_failed'),
            result.get('message', '文件解析失败'))
    return result


class KnowledgeIngestion:
    """同步请求只做一次显式摄取/索引；预约提交保证远端错误也有本机真实记录。"""

    def __init__(self, service, settings, parser=None):
        self.service, self.session, self.actor = service, service.session, service.actor
        self.settings, self.parser = settings, parser or parse_in_worker
        self.files = FileService(self.session, self.actor, settings, knowledge_write=True)

    def _new_document(self, space_id, name):
        self.actor.require('m08.write')
        self.service.space(space_id)
        name = name.strip()
        if not name or len(name) > 240:
            raise DomainError(422, 'document_name_required', '请填写240字以内的文档名称', {'name': '名称无效'})
        document = KnowledgeDocument(workspace_id=self.actor.workspace_id, space_id=space_id,
            name=name, source='file', status='parsing', created_by=self.actor.user_id,
            parser_metadata={'phase': 'parse'})
        self.session.add(document)
        self.session.flush()
        return document

    def upload(self, space_id, filename, content, content_type, name=None):
        """知识写用户可直接上传；原件保护引用与文档在同一预约事务提交。"""
        filename = Path(filename.replace('\\', '/')).name
        if Path(filename).suffix.lower() not in extractor.SUPPORTED_SUFFIXES:
            raise DomainError(422, 'document_format_unsupported', '请选择文本、PDF、DOCX、XLSX或PPTX文件')
        if not 0 < len(content) <= min(self.settings.max_upload_bytes, extractor.MAX_FILE_BYTES):
            raise DomainError(413, 'document_file_size', '请选择20MB以内的非空文件')
        document = self._new_document(space_id, name or filename)
        file = self.files.store_bytes(filename, content, content_type,
            source={'type': PROTECTED_REFERENCE, 'id': document.id}, visibility='workspace')
        document.file_id = file['id']
        document.source_sha256, document.source_revision = file['sha256'], file['revision']
        try:
            self.session.commit()
        except Exception:
            # 只清理由本次排他创建的文件；原有导入文件不走这里。
            self.session.rollback()
            path = (self.settings.files_dir / self.actor.workspace_id / file['id']).resolve()
            if path.is_relative_to(self.settings.files_dir.resolve()):
                path.unlink(missing_ok=True)
            raise
        return self._parse_and_index(document.id)

    def import_text(self, space_id, name, text, source='paste'):
        """粘贴与文件共用已提交正文和索引链，模型失败不会抹掉用户输入。"""
        from .service import MAX_DOCUMENT_CHARS, split_chunks
        self.actor.require('m08.write')
        self.service.space(space_id)
        body = text.strip()
        if not body:
            raise DomainError(422, 'document_text_required', '文档内容为空，无法入库')
        if len(body) > MAX_DOCUMENT_CHARS:
            raise DomainError(422, 'document_too_large', f'文档超过 {MAX_DOCUMENT_CHARS} 字上限')
        chunks = split_chunks(body)
        if not chunks or len(chunks) > MAX_CHUNKS:
            raise DomainError(422, 'document_chunk_limit', '文档分块过多，请拆分后提交')
        if not isinstance(source, str) or not source.strip() or len(source) > 40:
            raise DomainError(422, 'document_source_invalid', '文档来源格式无效', {'source': '来源无效'})
        document = self._new_document(space_id, name)
        document.source, document.status = source.strip(), 'parsed'
        document.char_count, document.chunk_count = len(body), len(chunks)
        document.parser_metadata = {'name': 'platform-text', 'version': 1,
            'format': 'text', 'blocks': 1, 'warnings': [], 'phase': 'index'}
        for ordinal, content in enumerate(chunks):
            self.session.add(KnowledgeChunk(workspace_id=self.actor.workspace_id,
                space_id=document.space_id, document_id=document.id, ordinal=ordinal,
                content=content, vector=[], source_location={}))
        self.session.commit()
        return self.index(document.id)

    def import_file(self, space_id, file_id, name=None):
        self.actor.require('m08.write')
        self.service.space(space_id)
        file = self.files._get(file_id)
        if Path(file.name).suffix.lower() not in extractor.SUPPORTED_SUFFIXES:
            raise DomainError(422, 'document_format_unsupported', '请选择文本、PDF、DOCX、XLSX或PPTX文件')
        # 重复点击同一来源不重复计费，也不自动重发此前失败/未知的索引。
        previous = self.session.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.workspace_id == self.actor.workspace_id,
            KnowledgeDocument.space_id == space_id, KnowledgeDocument.file_id == file.id,
            KnowledgeDocument.source_sha256 == file.sha256,
            KnowledgeDocument.source_revision == file.revision).order_by(KnowledgeDocument.created_at).limit(1))
        if previous:
            authorize_document_file(self.session, self.actor, previous, settings=self.settings, write=True)
            return self.service.document_dict(previous, write=True)
        document = self._new_document(space_id, name or file.name)
        document.file_id, document.source_sha256, document.source_revision = file.id, file.sha256, file.revision
        protected = self.session.scalar(select(ResourceReference.id).where(
            ResourceReference.workspace_id == self.actor.workspace_id,
            ResourceReference.target_type == 'file', ResourceReference.target_id == file.id,
            ResourceReference.source_type == PROTECTED_REFERENCE))
        self.session.add(ResourceReference(workspace_id=self.actor.workspace_id,
            source_type=PROTECTED_REFERENCE if protected else DOCUMENT_REFERENCE, source_id=document.id,
            target_type='file', target_id=file.id))
        self.session.commit()
        return self._parse_and_index(document.id)

    def _failure(self, document, exc, phase, *, expected_attempt=None):
        if expected_attempt and (document.parser_metadata or {}).get('index_attempt_id') != expected_attempt:
            raise DomainError(409, 'document_index_changed', '索引任务状态已变化，请刷新查看') from exc
        document.status = 'failed'
        known_error = isinstance(exc, (DomainError, extractor.ExtractionError))
        document.error = exc.message if known_error else '处理未完成，请检查配置后重新操作'
        document.parser_metadata = {**(document.parser_metadata or {}), 'phase': phase,
            'error_code': exc.code if known_error else 'document_processing_failed'}
        self.session.commit()
        return self.service.document_dict(document, write=True)

    def _parse_and_index(self, document_id):
        document = self.service.document_row(document_id, write=True)
        deadline = time.monotonic() + TOTAL_SECONDS
        try:
            file = authorize_document_file(self.session, self.actor, document, settings=self.settings, write=True)
            path = self.files.path(file.id)
            if path.stat().st_size > extractor.MAX_FILE_BYTES:
                raise DomainError(422, 'document_file_size', '原文件超过解析大小限制')
            if hashlib.sha256(path.read_bytes()).hexdigest() != document.source_sha256:
                raise DomainError(409, 'knowledge_source_changed', '原文件内容已变化，请重新上传')
            # 解析期间不持有SQLite读事务，便于其他工作区任务正常提交。
            self.session.commit()
            result = self.parser(path, file.name, min(PARSE_SECONDS, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                raise DomainError(504, 'document_parse_timeout', '文件解析超时')
            from .service import split_chunks
            chunks = [(content, block.get('location') or {}) for block in result['blocks']
                      for content in split_chunks(block['text'])]
            if not chunks or len(chunks) > MAX_CHUNKS:
                raise DomainError(422, 'document_chunk_limit', '文档分块过多，请拆分文件后上传')
            document = self.service.document_row(document_id, write=True)
            document.char_count = result['char_count']
            document.chunk_count = len(chunks)
            document.parser_metadata = {**result['metadata'], 'phase': 'index'}
            document.status = 'parsed'
            for ordinal, (content, location) in enumerate(chunks):
                self.session.add(KnowledgeChunk(workspace_id=self.actor.workspace_id,
                    space_id=document.space_id, document_id=document.id, ordinal=ordinal,
                    content=content, vector=[], source_location=location))
            self.session.commit()
        except SQLAlchemyError:
            # 数据库异常后只回滚并上抛，不能再查询/提交失败记录或自动补偿业务。
            self.session.rollback()
            raise
        except Exception as exc:
            self.session.rollback()
            document = self.service.document_row(document_id, write=True)
            return self._failure(document, exc, 'parse')
        return self.index(document_id, deadline=deadline)

    def index(self, document_id, *, deadline=None):
        """仅用户显式操作重建；批32、绝对60秒，不自动重试任何失败请求。"""
        self.actor.require('m08.write')
        document = self.service.document_row(document_id, write=True)
        expired_attempt = None
        if document.status == 'indexing':
            try:
                previous_deadline = datetime.fromisoformat((document.parser_metadata or {})['index_deadline_at'])
                expired = previous_deadline.tzinfo is not None and previous_deadline < datetime.now(timezone.utc)
            except (KeyError, ValueError, TypeError):
                expired = False
            if not expired:
                raise DomainError(409, 'document_index_busy', '文档正在建立索引，请稍后查看')
            # 仅这个显式重建端点可接替超期预约；不在列表/刷新时自动派发未知请求。
            expired_attempt = (document.parser_metadata or {}).get('index_attempt_id')
        chunks = list(self.session.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.workspace_id == self.actor.workspace_id,
            KnowledgeChunk.document_id == document.id).order_by(KnowledgeChunk.ordinal)))
        if not chunks or len(chunks) > MAX_CHUNKS:
            raise DomainError(422, 'document_not_parsed', '文档没有可索引正文，请重新上传')
        index_deadline = min(deadline or float('inf'), time.monotonic() + INDEX_SECONDS)
        attempt = str(uuid4())
        metadata = {**(document.parser_metadata or {}), 'phase': 'index', 'index_attempt_id': attempt,
            'index_started_at': datetime.now(timezone.utc).isoformat(),
            'index_deadline_at': (datetime.now(timezone.utc) + timedelta(seconds=INDEX_SECONDS)).isoformat()}
        metadata.pop('error_code', None)
        if expired_attempt:
            metadata['previous_unknown_attempt_id'] = expired_attempt
        reservation_conditions = [KnowledgeDocument.id == document.id,
            KnowledgeDocument.workspace_id == self.actor.workspace_id]
        if expired_attempt:
            reservation_conditions.extend([KnowledgeDocument.status == 'indexing',
                KnowledgeDocument.parser_metadata['index_attempt_id'].as_string() == expired_attempt])
        else:
            reservation_conditions.append(KnowledgeDocument.status != 'indexing')
        frozen_chunks = [(chunk.id, chunk.content) for chunk in chunks]
        reserved = self.session.execute(update(KnowledgeDocument).where(
            *reservation_conditions).values(status='indexing', error=None, parser_metadata=metadata))
        if reserved.rowcount != 1:
            raise DomainError(409, 'document_index_busy', '文档正在建立索引，请稍后查看')
        self.session.commit()
        try:
            vectors, dimension = [], None
            for offset in range(0, len(frozen_chunks), EMBEDDING_BATCH):
                remaining = index_deadline - time.monotonic()
                if remaining <= 0:
                    raise DomainError(504, 'document_index_timeout', '索引建立超时，请检查模型后重新操作')
                batch = frozen_chunks[offset:offset + EMBEDDING_BATCH]
                result = self.service.embeddings.embed([content for _, content in batch], timeout_seconds=remaining)
                if time.monotonic() >= index_deadline:
                    raise DomainError(504, 'document_index_timeout', '索引建立超时，请检查模型后重新操作')
                result, dimension = validate_vectors(result, len(batch), expected_dimension=dimension)
                vectors.extend(result)
            self.session.expire_all()
            document = self.service.document_row(document_id, write=True)
            if (document.parser_metadata or {}).get('index_attempt_id') != attempt:
                raise DomainError(409, 'document_index_changed', '索引任务状态已变化，请刷新查看')
            current_chunks = list(self.session.scalars(select(KnowledgeChunk).where(
                KnowledgeChunk.workspace_id == self.actor.workspace_id,
                KnowledgeChunk.document_id == document.id).order_by(KnowledgeChunk.ordinal)))
            if [(chunk.id, chunk.content) for chunk in current_chunks] != frozen_chunks:
                raise DomainError(409, 'knowledge_source_changed', '索引期间文档正文已变化，请重新操作')
            for chunk, vector in zip(current_chunks, vectors, strict=True):
                chunk.vector = vector
            document.status, document.error = 'ready', None
            document.parser_metadata = {**document.parser_metadata, 'phase': 'ready', 'embedding_dimension': dimension}
            self.session.commit()
            return self.service.document_dict(document, write=True)
        except SQLAlchemyError:
            self.session.rollback()
            raise
        except Exception as exc:
            self.session.rollback()
            document = self.service.document_row(document_id, write=True)
            return self._failure(document, exc, 'index', expected_attempt=attempt)
