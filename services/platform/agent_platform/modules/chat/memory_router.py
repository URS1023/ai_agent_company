"""个人记忆接口；当前身份沿用平台逐请求权限，不接收客户端所属字段。"""
from fastapi import APIRouter, Depends, Query

from agent_platform.core.context import require_permission
from agent_platform.core.database import get_session
from .memories import MemoryCreate, MemoryPatch, MemoryService, MemoryType


router = APIRouter(prefix="/memories", tags=["个人记忆"])


@router.get("")
def memories(q: str = Query(default="", max_length=200), type: MemoryType | None = None,
             limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
             session=Depends(get_session), actor=Depends(require_permission("m02.read"))):
    return MemoryService(session, actor).list(q, type=type, limit=limit, offset=offset)


@router.post("", status_code=201)
def create_memory(data: MemoryCreate, session=Depends(get_session), actor=Depends(require_permission("m02.write"))):
    return MemoryService(session, actor).create(data)


@router.get("/{id}")
def memory(id: str, session=Depends(get_session), actor=Depends(require_permission("m02.read"))):
    return MemoryService(session, actor).get(id)


@router.patch("/{id}")
def update_memory(id: str, data: MemoryPatch, session=Depends(get_session), actor=Depends(require_permission("m02.write"))):
    return MemoryService(session, actor).update(id, data)


@router.delete("/{id}")
def delete_memory(id: str, revision: int = Query(ge=1), session=Depends(get_session),
                  actor=Depends(require_permission("m02.delete"))):
    return MemoryService(session, actor).delete(id, revision)
