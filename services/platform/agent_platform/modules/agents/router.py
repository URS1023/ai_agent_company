"""智能体应用接口：配置管理与流式回答。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor, require_permission
from agent_platform.core.database import get_session

from .service import AgentService

router = APIRouter(prefix='/api/v1/agents', tags=['智能体应用'])


class AgentInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=2000)
    prompt: str = Field(default='', max_length=20000)
    space_ids: list[str] = Field(default_factory=list, max_length=10)
    top_k: int = Field(default=3, ge=1, le=10)
    max_tokens: int = Field(default=1200, ge=64, le=4096)
    temperature: float = Field(default=0.3, ge=0, le=2)


class QuestionInput(BaseModel):
    question: str = Field(min_length=1, max_length=8000)


@router.get('')
def list_agents(actor: Actor = Depends(require_permission('m03.read')),
                session: Session = Depends(get_session)) -> dict:
    return AgentService(session, actor).list()


@router.post('', status_code=201)
def create_agent(body: AgentInput, actor: Actor = Depends(require_permission('m03.write')),
                 session: Session = Depends(get_session)) -> dict:
    return AgentService(session, actor).create(body.name, body.description, body.prompt, body.space_ids,
                                               body.top_k, body.max_tokens, body.temperature)


@router.get('/{agent_id}')
def get_agent(agent_id: str, actor: Actor = Depends(require_permission('m03.read')),
              session: Session = Depends(get_session)) -> dict:
    return AgentService(session, actor).detail(agent_id)


@router.put('/{agent_id}')
def update_agent(agent_id: str, body: AgentInput, actor: Actor = Depends(require_permission('m03.write')),
                 session: Session = Depends(get_session)) -> dict:
    return AgentService(session, actor).update(agent_id, body.name, body.description, body.prompt,
                                               body.space_ids, body.top_k, body.max_tokens, body.temperature)


@router.delete('/{agent_id}')
def delete_agent(agent_id: str, actor: Actor = Depends(require_permission('m03.delete')),
                 session: Session = Depends(get_session)) -> dict:
    return AgentService(session, actor).delete(agent_id)


@router.post('/{agent_id}/messages')
def stream_answer(agent_id: str, body: QuestionInput,
                  actor: Actor = Depends(require_permission('m03.write')),
                  session: Session = Depends(get_session)) -> StreamingResponse:
    opened = AgentService(session, actor).stream(agent_id, body.question)
    return StreamingResponse(iter(opened), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'},
                             background=BackgroundTask(opened.close))


@router.post('/{agent_id}/answers')
def answer(agent_id: str, body: QuestionInput, actor: Actor = Depends(require_permission('m03.read')),
           session: Session = Depends(get_session)) -> dict:
    return AgentService(session, actor).answer(agent_id, body.question)
