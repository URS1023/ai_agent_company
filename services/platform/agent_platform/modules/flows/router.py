"""工作流接口：定义管理、运行与运行记录。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor, require_permission
from agent_platform.core.database import get_session

from .service import WorkflowService

router = APIRouter(prefix='/api/v1/workflows', tags=['工作流'])


class WorkflowInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=2000)
    definition: dict[str, Any] = Field(default_factory=dict)
    revision: int | None = Field(default=None, ge=1)


class RunInput(BaseModel):
    input: str = Field(default='', max_length=8000)
    inputs: dict[str, Any] | None = None


@router.get('')
def list_workflows(actor: Actor = Depends(require_permission('m04.read')),
                   session: Session = Depends(get_session)) -> dict:
    return WorkflowService(session, actor).list()


@router.post('', status_code=201)
def create_workflow(body: WorkflowInput, actor: Actor = Depends(require_permission('m04.write')),
                    session: Session = Depends(get_session)) -> dict:
    return WorkflowService(session, actor).create(body.name, body.description, body.definition)


@router.get('/{workflow_id}')
def get_workflow(workflow_id: str, actor: Actor = Depends(require_permission('m04.read')),
                 session: Session = Depends(get_session)) -> dict:
    return WorkflowService(session, actor).detail(workflow_id)


@router.put('/{workflow_id}')
def update_workflow(workflow_id: str, body: WorkflowInput,
                    actor: Actor = Depends(require_permission('m04.write')),
                    session: Session = Depends(get_session)) -> dict:
    return WorkflowService(session, actor).update(workflow_id, body.name, body.description, body.definition,
        expected_revision=body.revision)


@router.delete('/{workflow_id}')
def delete_workflow(workflow_id: str, actor: Actor = Depends(require_permission('m04.delete')),
                    session: Session = Depends(get_session)) -> dict:
    return WorkflowService(session, actor).delete(workflow_id)


@router.get('/{workflow_id}/runs')
def list_runs(workflow_id: str, actor: Actor = Depends(require_permission('m04.read')),
              session: Session = Depends(get_session),
              idempotency_key: str | None = Query(default=None, max_length=160)) -> dict:
    return WorkflowService(session, actor).runs(workflow_id, idempotency_key=idempotency_key)


@router.post('/{workflow_id}/runs', status_code=201)
def run_workflow(workflow_id: str, body: RunInput, actor: Actor = Depends(require_permission('m04.write')),
                 session: Session = Depends(get_session),
                 idempotency_key: str | None = Header(default=None, alias='Idempotency-Key', max_length=160)) -> dict:
    return WorkflowService(session, actor).run(workflow_id, body.input, idempotency_key=idempotency_key, inputs=body.inputs)


@router.get('/runs/{run_id}')
def get_run(run_id: str, actor: Actor = Depends(require_permission('m04.read')),
            session: Session = Depends(get_session)) -> dict:
    return WorkflowService(session, actor).run_detail(run_id)


@router.get('/runs/{run_id}/events')
def get_run_events(run_id: str, actor: Actor = Depends(require_permission('m04.read')),
                   session: Session = Depends(get_session), after: int = Query(default=0, ge=0),
                   limit: int = Query(default=100, ge=1, le=200)) -> dict:
    return WorkflowService(session, actor).run_events(run_id, after=after, limit=limit)
