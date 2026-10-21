"""原生工具专用入口：引擎认证不代表业务授权，每次重新解析人类身份。"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from agent_platform.core.context import get_actor
from agent_platform.core.database import get_session
from .delegated_contract import PREFIX, delegated_tool_spec
from .delegation_auth import engine_identity, resolve_delegated_actor
from .delegated_tools import execute_tool

router = APIRouter(prefix=PREFIX.removeprefix("/api/v1"), tags=["可信业务工具"])


@router.get("/openapi.json", include_in_schema=False)
def schema(request: Request, session=Depends(get_session), actor=Depends(get_actor)):
    # 导入工具目录时尚无当前turn，只允许证书引擎读取无业务内容的固定契约。
    engine_identity(session, actor, request)
    return JSONResponse(delegated_tool_spec(actor), headers={"Cache-Control": "no-store"})


@router.get("/{operation_id}")
def invoke(operation_id: str, request: Request, session=Depends(get_session), engine=Depends(get_actor)):
    actor, context = resolve_delegated_actor(session, engine, request)
    from .delegated_budget import reserve_tool_call
    if reserve_tool_call(session, actor, context, operation_id):
        # 额度已提交，后续失败仍保留消费；新事务重核证书与人类授权，不能重用旧快照。
        engine = get_actor(request, session)
        actor, context = resolve_delegated_actor(session, engine, request)
    payload, sources = execute_tool(session, actor, operation_id, request.query_params,
        context=context, settings=getattr(request.app.state, 'settings', None))
    if operation_id == 'search_platform_knowledge':
        # 向量等待跨越权限变更边界。此阶段只有读取，先结束旧快照再核当前主体与来源，
        # 不能把等待前的人员/证书权限继续用于向原生模型交付正文。
        session.rollback()
        engine = get_actor(request, session)
        actor, context = resolve_delegated_actor(session, engine, request)
        from .delegated_sources import authorize_delegated_source
        for source in sources:
            authorize_delegated_source(session, actor, source)
    from ..chat.native_sources import append_turn_sources
    append_turn_sources(session, context, sources)
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})
