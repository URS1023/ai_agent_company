"""统一原生聊天入口；浏览器只使用平台ID及会话Cookie，不接触原生JWT。"""

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Literal
import json
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from agent_platform.core.context import Actor, require_permission
from agent_platform.core.database import get_session
from .native_service import NativeChatService, identity_dict
from .native_turns import NativeTurnService
from .native_events import native_chat_lifespan, stream_events
from agent_platform.core.errors import DomainError

router = APIRouter(prefix='/native', tags=['原生聊天会话'], lifespan=native_chat_lifespan)


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class IdentityInput(Input):
    integration_id: str = Field(min_length=1, max_length=64)


class ConversationInput(Input):
    identity_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default='新对话', min_length=1, max_length=200)


class RevisionInput(Input):
    revision: int = Field(ge=1)


class RenameInput(RevisionInput):
    title: str = Field(min_length=1, max_length=200)


class MessageInput(RevisionInput):
    content: str = Field(min_length=1, max_length=40000)


class ForkInput(RevisionInput):
    message_id: str = Field(min_length=1, max_length=100)


class TurnInput(RevisionInput):
    model_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=40000)
    parent_message_id: str | None = Field(default=None, min_length=1, max_length=100)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    temperature: float = Field(default=0.7, ge=0, le=1, allow_inf_nan=False)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)


class ResolutionInput(Input):
    call_id: str = Field(min_length=1, max_length=200)
    action: Literal['approve', 'reject', 'answer']
    answers: dict[str, Any] | None = None


@router.get('/identities/{identity_id}/models')
def available_models(identity_id: str, request: Request, actor: Actor = Depends(require_permission('m02.read')),
                     session: Session = Depends(get_session)):
    return NativeTurnService(session, actor, request).models(identity_id)


@router.post('/conversations/{chat_id}/turns', status_code=202)
def start_turn(chat_id: str, body: TurnInput, request: Request, actor: Actor = Depends(require_permission('m02.write')),
               session: Session = Depends(get_session),
               key: str | None = Header(default=None, alias='Idempotency-Key', max_length=160)):
    return NativeTurnService(session, actor, request).start(chat_id, body.model_dump(), key)


@router.get('/conversations/{chat_id}/turns')
def list_turns(chat_id: str, request: Request, actor: Actor = Depends(require_permission('m02.read')),
               session: Session = Depends(get_session),
               idempotency_key: str | None = Query(default=None, max_length=160)):
    return NativeTurnService(session, actor, request).list_turns(chat_id, key=idempotency_key)


@router.get('/turns/{turn_id}')
def turn_detail(turn_id: str, request: Request, actor: Actor = Depends(require_permission('m02.read')),
                session: Session = Depends(get_session)):
    return NativeTurnService(session, actor, request).detail_turn(turn_id)


@router.post('/turns/{turn_id}/reconcile')
def reconcile_turn(turn_id: str, request: Request, actor: Actor = Depends(require_permission('m02.write')),
                   session: Session = Depends(get_session)):
    return NativeTurnService(session, actor, request).reconcile(turn_id)


@router.get('/turns/{turn_id}/events')
def turn_events(turn_id: str, request: Request, actor: Actor = Depends(require_permission('m02.read')),
                session: Session = Depends(get_session), after: int = Query(default=0, ge=0)):
    NativeTurnService(session, actor, request).turn(turn_id)
    session.commit()
    return StreamingResponse(stream_events(request, actor, turn_id, after), media_type='text/event-stream',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@router.post('/turns/{turn_id}/stop', status_code=202)
def stop_turn(turn_id: str, body: Input, request: Request, actor: Actor = Depends(require_permission('m02.write')),
              session: Session = Depends(get_session),
              key: str | None = Header(default=None, alias='Idempotency-Key', max_length=160)):
    return NativeTurnService(session, actor, request).action(turn_id, 'stop', body.model_dump(), key)


@router.post('/turns/{turn_id}/resolutions', status_code=202)
def resolve_turn(turn_id: str, body: ResolutionInput, request: Request,
                 actor: Actor = Depends(require_permission('m02.write')), session: Session = Depends(get_session),
                 key: str | None = Header(default=None, alias='Idempotency-Key', max_length=160)):
    payload = body.model_dump(exclude_none=True)
    try:
        size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode())
    except (ValueError, TypeError, RecursionError):
        raise DomainError(422, 'native_chat_answers_invalid', '确认内容格式不正确') from None
    if size > 32768:
        raise DomainError(422, 'native_chat_answers_budget_exceeded', '本次确认内容过多')
    return NativeTurnService(session, actor, request).action(turn_id, 'resolve', payload, key)


@router.get('/instances')
def instances(request: Request, actor: Actor = Depends(require_permission('m02.read')),
              session: Session = Depends(get_session)):
    return NativeChatService(session, actor, request).instances()


@router.post('/identities', status_code=201)
def provision_identity(body: IdentityInput, request: Request, actor: Actor = Depends(require_permission('m02.write')),
                       session: Session = Depends(get_session)):
    return NativeChatService(session, actor, request).provision_identity(body.integration_id)


@router.get('/identities/{identity_id}')
def identity(identity_id: str, request: Request, actor: Actor = Depends(require_permission('m02.read')),
             session: Session = Depends(get_session)):
    return identity_dict(NativeChatService(session, actor, request).identity(identity_id)[0])


@router.post('/identities/{identity_id}/reconcile')
def reconcile_identity(identity_id: str, request: Request, actor: Actor = Depends(require_permission('m02.write')),
                       session: Session = Depends(get_session)):
    return NativeChatService(session, actor, request).reconcile_identity(identity_id)


@router.get('/conversations')
def conversations(request: Request, actor: Actor = Depends(require_permission('m02.read')),
                  session: Session = Depends(get_session), limit: int = Query(default=50, ge=1, le=100),
                  offset: int = Query(default=0, ge=0)):
    return NativeChatService(session, actor, request).list(limit=limit, offset=offset)


@router.post('/conversations', status_code=201)
def create_conversation(body: ConversationInput, request: Request, actor: Actor = Depends(require_permission('m02.write')),
                        session: Session = Depends(get_session),
                        key: str | None = Header(default=None, alias='Idempotency-Key', max_length=160)):
    return NativeChatService(session, actor, request).create(body.identity_id, body.title, idempotency_key=key)


@router.get('/conversations/{chat_id}')
def conversation(chat_id: str, request: Request, actor: Actor = Depends(require_permission('m02.read')),
                 session: Session = Depends(get_session)):
    return NativeChatService(session, actor, request).detail(chat_id)


@router.patch('/conversations/{chat_id}')
def rename(chat_id: str, body: RenameInput, request: Request, actor: Actor = Depends(require_permission('m02.write')),
           session: Session = Depends(get_session)):
    return NativeChatService(session, actor, request).edit(chat_id, body.revision, title=body.title)


@router.patch('/conversations/{chat_id}/messages/{message_id}')
def edit_message(chat_id: str, message_id: str, body: MessageInput, request: Request,
                 actor: Actor = Depends(require_permission('m02.write')), session: Session = Depends(get_session)):
    return NativeChatService(session, actor, request).edit(chat_id, body.revision, message_id=message_id, content=body.content)


@router.post('/conversations/{chat_id}/forks', status_code=201)
def fork(chat_id: str, body: ForkInput, request: Request, actor: Actor = Depends(require_permission('m02.write')),
         session: Session = Depends(get_session),
         key: str | None = Header(default=None, alias='Idempotency-Key', max_length=160)):
    return NativeChatService(session, actor, request).fork(chat_id, body.revision, body.message_id, idempotency_key=key)


@router.delete('/conversations/{chat_id}')
def delete(chat_id: str, body: RevisionInput, request: Request,
           actor: Actor = Depends(require_permission('m02.delete')), session: Session = Depends(get_session)):
    return NativeChatService(session, actor, request).delete(chat_id, body.revision)
