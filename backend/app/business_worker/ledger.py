"""模型调用与真实用量的独立台账，失败也可有用量。"""
from __future__ import annotations

import time
from datetime import datetime

from app.ids import new_id
from app.models import ModelCall, Run
from app.errors import NotFoundError
from .context import session, lock_for


def authorize(run_id=None, provider="", model="", purpose="conversation", workspace_id=None, profile=None,
              capability="chat", usage_context=None):
    from app.modules.model.service import enforce_budget
    from app.modules.runtime.service import create_run
    from .chat import run_actor
    with session() as db:
        if run_id:
            run = db.get(Run, run_id)
            if run is None:
                raise NotFoundError("模型所属业务运行不存在")
            run_actor(db, run)
            workspace_id = run.workspace_id
        else:
            from app.models import WorkspaceMember
            from sqlalchemy import select
            query = select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
            if (usage_context or {}).get("actor_user_id"):
                query = query.where(WorkspaceMember.user_id == usage_context["actor_user_id"])
            member = db.scalar(query)
            if not member:
                raise NotFoundError("后台模型调用缺少有效工作区")
            run = create_run(db, workspace_id=workspace_id, conversation_id="", user_id=member.user_id,
                             model_profile=profile or "reasoning", context_snapshot={"execution_scope": "standalone", "execution_owner": "dsh-host", "chat_engine": "dsh", "dsh_model_only": True})
        enforce_budget(workspace_id)
        from app.providers.model import _profile_config
        resolved_profile = profile or run.model_profile or "reasoning"
        config = _profile_config(resolved_profile, workspace_id)
        row = ModelCall(id=new_id(), workspace_id=workspace_id, run_id=run.id, provider=provider or "dsh",
                        model=model or "configured", status="started", started_at=datetime.utcnow(),
                        metadata_json={"purpose": purpose, "engine": "dsh", "profile": resolved_profile,
                                       "capability": capability, "usage_context": usage_context or {},
                                       "usage_provider": config.get("provider"),
                                       "pricing": (config.get("capabilities") or {}).get("pricing")})
        db.add(row)
        db.commit()
        return {"allowed": True, "model_call_id": row.id, "run_id": run.id}


def settle(run_id, model_call_id, status, usage=None, error=None):
    from app.providers.model import _persist_usage
    with lock_for("model:" + model_call_id), session() as db:
        row = db.get(ModelCall, model_call_id)
        run = db.get(Run, run_id)
        if not row or not run or row.run_id != run_id:
            raise NotFoundError("模型调用不存在")
        if row.status != "started":
            return {"status": row.status, "duplicate": True}
        row.status = "completed" if status == "completed" else "failed"
        row.completed_at = datetime.utcnow()
        row.usage_json = usage
        row.error_code = (error or {}).get("code") if isinstance(error, dict) else str(error) if error else None
        db.commit()
        elapsed = max(0, (row.completed_at - row.started_at).total_seconds())
        _persist_usage(workspace_id=run.workspace_id, profile_name=row.metadata_json.get("profile") or run.model_profile,
                       cfg={"provider": row.metadata_json.get("usage_provider") or row.provider, "model": row.model,
                            "capabilities": {"pricing": row.metadata_json.get("pricing")}},
                       capability=row.metadata_json.get("capability") or "chat", usage=usage or {},
                       started=time.perf_counter() - elapsed, status=row.status,
                       usage_context={**(row.metadata_json.get("usage_context") or {}), "run_id": run.id,
                                      "model_call_id": row.id, "purpose": row.metadata_json.get("purpose")},
                       error_code=row.error_code)
        if (run.context_snapshot or {}).get("dsh_model_only"):
            from app.modules.runtime.service import finalize_run
            finalize_run(db, run, status=row.status, model_usage=usage)
            db.commit()
        return {"status": row.status, "model_call_id": row.id}
