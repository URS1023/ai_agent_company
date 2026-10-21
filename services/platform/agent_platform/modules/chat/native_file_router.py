"""聊天附件只接平台文件ID或实际上传字节；浏览器不能指定路径、原生ID或外部URL。"""

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from agent_platform.core.context import require_permission
from agent_platform.core.database import get_session
from .native_files import MAX_BYTES, NativeFileService, file_dict

router = APIRouter(prefix='/native', tags=['原生聊天附件'])


class ImportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    file_id: str = Field(min_length=1, max_length=36)


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)


@router.post('/identities/{identity_id}/uploads', status_code=201)
def upload(identity_id: str, request: Request, file: UploadFile = File(...),
           key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160),
           actor=Depends(require_permission('m02.write')), session=Depends(get_session)):
    content = file.file.read(MAX_BYTES + 1)
    return NativeFileService(session, actor, request).upload(identity_id, file.filename or '', content, key)


@router.post('/identities/{identity_id}/file-imports', status_code=201)
def import_file(identity_id: str, body: ImportInput, request: Request,
                key: str = Header(alias='Idempotency-Key', min_length=1, max_length=160),
                actor=Depends(require_permission('m02.write')), session=Depends(get_session)):
    return NativeFileService(session, actor, request).import_file(identity_id, body.file_id, key)


@router.get('/files')
def files(request: Request, identity_id: str | None = None, limit: int = Query(50, ge=1, le=100),
          offset: int = Query(0, ge=0), actor=Depends(require_permission('m02.read')), session=Depends(get_session)):
    return NativeFileService(session, actor, request).list(identity_id=identity_id, limit=limit, offset=offset)


@router.get('/files/{mapping_id}')
def file(mapping_id: str, request: Request, actor=Depends(require_permission('m02.read')), session=Depends(get_session)):
    return file_dict(NativeFileService(session, actor, request).get(mapping_id))


@router.get('/files/{mapping_id}/source')
def source(mapping_id: str, request: Request, actor=Depends(require_permission('m02.read')), session=Depends(get_session)):
    service = NativeFileService(session, actor, request)
    row = service.get(mapping_id)
    return FileResponse(service.files.path(row.file_id), media_type=row.source_content_type, filename=row.source_name,
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.post('/files/{mapping_id}/refreshes')
def refresh(mapping_id: str, body: RevisionInput, request: Request,
            actor=Depends(require_permission('m02.write')), session=Depends(get_session)):
    return NativeFileService(session, actor, request).refresh(mapping_id, body.revision)


@router.post('/files/{mapping_id}/reconciliations')
def reconcile(mapping_id: str, body: RevisionInput, request: Request,
              actor=Depends(require_permission('m02.write')), session=Depends(get_session)):
    return NativeFileService(session, actor, request).reconcile(mapping_id, body.revision)


@router.delete('/files/{mapping_id}')
def remove(mapping_id: str, body: RevisionInput, request: Request,
           actor=Depends(require_permission('m02.delete')), session=Depends(get_session)):
    return NativeFileService(session, actor, request).remove(mapping_id, body.revision)
