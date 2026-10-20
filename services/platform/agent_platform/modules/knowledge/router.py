"""知识库接口：空间、文档与检索，全部由平台自身实现。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor, require_permission
from agent_platform.core.database import get_session
from agent_platform.core.errors import DomainError
from agent_platform.core.files import FileService
from agent_platform.core.settings import get_settings

from .service import KnowledgeService
from .ingestion import KnowledgeIngestion

router = APIRouter(prefix='/api/v1/kb', tags=['知识库'])


class SpaceInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=2000)


class DocumentInput(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1)
    source: str = Field(default='paste', max_length=40)


class SearchInput(BaseModel):
    space_ids: list[str] = Field(min_length=1, max_length=10)
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class FileImportInput(BaseModel):
    file_id: str = Field(min_length=1, max_length=36)
    name: str | None = Field(default=None, min_length=1, max_length=240)


@router.get('/spaces')
def list_spaces(actor: Actor = Depends(require_permission('m08.read')), session: Session = Depends(get_session)) -> dict:
    return KnowledgeService(session, actor).spaces()


@router.post('/spaces', status_code=201)
def create_space(body: SpaceInput, actor: Actor = Depends(require_permission('m08.write')),
                 session: Session = Depends(get_session)) -> dict:
    return KnowledgeService(session, actor).create_space(body.name, body.description)


@router.delete('/spaces/{space_id}')
def delete_space(space_id: str, actor: Actor = Depends(require_permission('m08.delete')),
                 session: Session = Depends(get_session)) -> dict:
    return KnowledgeService(session, actor).delete_space(space_id)


@router.get('/spaces/{space_id}/documents')
def list_documents(space_id: str, q: str = Query('', max_length=200),
                   limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                   actor: Actor = Depends(require_permission('m08.read')),
                   session: Session = Depends(get_session)) -> dict:
    return KnowledgeService(session, actor).documents(space_id, q, limit, offset)


@router.post('/spaces/{space_id}/documents', status_code=201)
def import_document(space_id: str, body: DocumentInput,
                    actor: Actor = Depends(require_permission('m08.write')),
                    session: Session = Depends(get_session)) -> dict:
    return KnowledgeService(session, actor).import_text(space_id, body.name, body.text, body.source)


@router.delete('/documents/{document_id}')
def delete_document(document_id: str, actor: Actor = Depends(require_permission('m08.delete')),
                    session: Session = Depends(get_session)) -> dict:
    return KnowledgeService(session, actor).delete_document(document_id)


@router.post('/spaces/{space_id}/uploads', status_code=201)
def upload_document(space_id: str, request: Request, file: UploadFile,
                    name: str | None = Form(default=None),
                    actor: Actor = Depends(require_permission('m08.write')),
                    session: Session = Depends(get_session)) -> dict:
    """知识写入口直接复用平台原件存储；文档状态区分保存成功与索引完成。"""
    settings = get_settings(request)
    content = file.file.read(min(settings.max_upload_bytes, 20 * 1024 * 1024) + 1)
    service = KnowledgeService(session, actor, settings=settings)
    return KnowledgeIngestion(service, settings).upload(space_id, file.filename or '', content,
        file.content_type or 'application/octet-stream', name)


@router.post('/spaces/{space_id}/file-imports', status_code=201)
def import_existing_file(space_id: str, body: FileImportInput, request: Request,
                         actor: Actor = Depends(require_permission('m08.write')),
                         session: Session = Depends(get_session)) -> dict:
    settings = get_settings(request)
    return KnowledgeIngestion(KnowledgeService(session, actor, settings=settings), settings).import_file(
        space_id, body.file_id, body.name)


@router.get('/documents/{document_id}')
def document_detail(document_id: str, actor: Actor = Depends(require_permission('m08.read')),
                    session: Session = Depends(get_session)) -> dict:
    return KnowledgeService(session, actor).document(document_id)


@router.get('/documents/{document_id}/chunks')
def document_chunks(document_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                    actor: Actor = Depends(require_permission('m08.read')),
                    session: Session = Depends(get_session)) -> dict:
    return KnowledgeService(session, actor).chunks(document_id, limit=limit, offset=offset)


@router.get('/documents/{document_id}/source')
def original_document(document_id: str, request: Request,
                       actor: Actor = Depends(require_permission('m08.read')),
                       session: Session = Depends(get_session)):
    settings = get_settings(request)
    document = KnowledgeService(session, actor, settings=settings).document_row(document_id)
    if document.file_id is None:
        raise DomainError(404, 'document_no_original', '此文档没有上传的原文件')
    files = FileService(session, actor, settings)
    file = files.get(document.file_id)
    return FileResponse(files.path(file['id']), filename=file['name'], media_type=file['content_type'],
        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                 'Content-Security-Policy': "default-src 'none'; sandbox"})


@router.post('/documents/{document_id}/index-jobs')
def index_document(document_id: str, request: Request,
                    actor: Actor = Depends(require_permission('m08.write')),
                    session: Session = Depends(get_session)) -> dict:
    settings = get_settings(request)
    return KnowledgeIngestion(KnowledgeService(session, actor, settings=settings), settings).index(document_id)


@router.post('/search')
def search(body: SearchInput, actor: Actor = Depends(require_permission('m08.read')),
           session: Session = Depends(get_session)) -> dict:
    return KnowledgeService(session, actor).search(body.space_ids, body.query, body.top_k)
