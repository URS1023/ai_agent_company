"""安全凭据引用的管理接口；删除前检查实际依赖。"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from ...core.context import Actor, require_permission
from ...core.database import get_session
from ...core.errors import DomainError
from ...core.records import RecordStore

router = APIRouter()


class SecretInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    name: str = Field(default='凭据', min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=65536)
    kind: str = Field(default='api_key', min_length=1, max_length=80)

    @field_validator('name', 'kind')
    @classmethod
    def trim_metadata(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('名称和类型不能为空')
        return value


class SecretUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: str = Field(min_length=1, max_length=65536)
    revision: int = Field(ge=1)


class TicketInput(SecretUpdate):
    ttl_seconds: int = Field(default=120, ge=30, le=300)


class TicketConsume(BaseModel):
    model_config = ConfigDict(extra='forbid')
    vault_import_ticket: str = Field(min_length=20, max_length=200)


@router.get('/secret-references')
def secret_list(request: Request, actor: Actor = Depends(require_permission('m15.read')),
                 session: Session = Depends(get_session)):
    rows = request.app.state.secret_vault.list(actor.workspace_id, session=session)
    return {'items': rows, 'total': len(rows)}


@router.post('/secret-references', status_code=201)
def secret_create(body: SecretInput, request: Request, actor: Actor = Depends(require_permission('m15.admin')),
                   session: Session = Depends(get_session)):
    result = request.app.state.secret_vault.put(body.value, workspace_id=actor.workspace_id,
        actor_id=actor.user_id, name=body.name, kind=body.kind, session=session)
    RecordStore(session, actor).audit('secret.create', 'secret_reference', result['id'], {'name': body.name})
    return result


@router.patch('/secret-references/{id}')
def secret_rotate(id: str, body: SecretUpdate, request: Request, actor: Actor = Depends(require_permission('m15.admin')),
                   session: Session = Depends(get_session)):
    result = request.app.state.secret_vault.rotate(id, body.value, actor.workspace_id,
        actor.user_id, body.revision, session=session)
    RecordStore(session, actor).audit('secret.rotate', 'secret_reference', id)
    return result


@router.post('/secret-references/{id}/rotation-tickets', status_code=201)
def rotation_ticket(id: str, body: TicketInput, request: Request,
                    actor: Actor = Depends(require_permission('m15.admin')), session: Session = Depends(get_session)):
    result = request.app.state.secret_vault.rotation_ticket(id, body.value, actor.workspace_id,
        actor.user_id, body.revision, ttl_seconds=body.ttl_seconds, session=session)
    RecordStore(session, actor).audit('secret.rotation_ticket', 'secret_reference', id)
    return result


@router.post('/secret-references/{id}/rotations')
def consume_ticket(id: str, body: TicketConsume, request: Request,
                    actor: Actor = Depends(require_permission('m15.admin')), session: Session = Depends(get_session)):
    result = request.app.state.secret_vault.consume_rotation(id, body.vault_import_ticket,
        actor.workspace_id, actor.user_id, session=session)
    RecordStore(session, actor).audit('secret.rotate', 'secret_reference', id, {'revision': result['revision']})
    return result


@router.delete('/secret-references/{id}')
def secret_delete(id: str, request: Request, actor: Actor = Depends(require_permission('m15.admin')),
                   session: Session = Depends(get_session)):
    from ..integrations.service import IntegrationService
    references = IntegrationService(session, actor, request).credential_references(id)
    if references:
        raise DomainError(409, 'secret_in_use', '凭据仍被连接或运行使用，请先处理依赖', {'references': references})
    request.app.state.secret_vault.delete(id, actor.workspace_id, actor.user_id, session=session)
    RecordStore(session, actor).audit('secret.delete', 'secret_reference', id)
    return {'id': id, 'deleted': True}
