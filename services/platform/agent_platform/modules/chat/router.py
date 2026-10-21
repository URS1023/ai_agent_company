"""对话接口：模型信息与流式补全。鉴权沿用平台权限，密钥不出后端。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from agent_platform.core.context import Actor, require_permission

from . import schemas as dto
from .service import ChatService
from .memory_router import router as memory_router
from .native_router import router as native_router
from .native_file_router import router as native_file_router

router = APIRouter(prefix='/api/v1/chat', tags=['原生对话'])
router.include_router(memory_router)
router.include_router(native_router)
router.include_router(native_file_router)


@router.get('/profile')
def profile(actor: Actor = Depends(require_permission('m02.read'))) -> dict:
    """当前对话模型；供页面显示模型名并判断是否可用。"""
    return ChatService().profile()


@router.post('/completions')
def completions(body: dto.ChatRequest, request: Request,
                actor: Actor = Depends(require_permission('m02.write'))) -> StreamingResponse:
    """流式对话；上游错误在开始流之前就以正常状态码返回。"""
    opened = ChatService().open_stream([m.model_dump() for m in body.messages],
                                       max_tokens=body.max_tokens, temperature=body.temperature,
                                       session=body.session)
    return StreamingResponse(iter(opened), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@router.post('/answers')
def answers(body: dto.ChatRequest, actor: Actor = Depends(require_permission('m02.read'))) -> dict:
    """一次性补全；供需要完整结果的按钮类操作使用。"""
    return ChatService().complete([m.model_dump() for m in body.messages],
                                  max_tokens=body.max_tokens, temperature=body.temperature, session=body.session)
