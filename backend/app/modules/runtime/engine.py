"""固定每次运行的引擎选择，禁止任务执行中静默切换。"""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def selected_engine(run=None) -> str:
    """已建单运行读取自己的快照，新运行才读取部署默认值。"""
    snapshot = (run.context_snapshot or {}) if run is not None else {}
    value = snapshot.get("chat_engine") or settings.CHAT_ENGINE
    if value not in {"openwebui", "harness"}:
        raise ValueError("CHAT_ENGINE 必须为 openwebui 或 harness")
    return value


def run_chat(db, run, workspace, user, **kwargs):
    """只分派一次；异常交给当前引擎归档，不回退或重放业务任务。"""
    engine = selected_engine(run)
    run.context_snapshot = {**(run.context_snapshot or {}), "chat_engine": engine}
    db.commit()
    if engine == "harness":
        from app.modules.harness.runtime import run_chat as execute
    else:
        # openwebui 引擎不使用 runtime_options（评审 R18）：发现模型配置了高级运行
        # 选项时告警，避免管理员误以为协议/推理等级在新引擎之外也已生效。
        try:
            from app.providers.model import _profile_config
            cfg = _profile_config(run.model_profile or "reasoning", run.workspace_id)
            if (cfg.get("capabilities") or {}).get("runtime_options"):
                logger.warning("openwebui 引擎忽略模型 runtime_options（run=%s）：协议/推理/预算选项不会生效", run.id)
        except Exception:
            pass
        from app.modules.openwebui.runtime import run_chat as execute
    yield from execute(db, run, workspace, user, **kwargs)


def runtime_health() -> dict:
    """检查当前选定运行时；Harness 按需启动且不在健康检查中调用模型。"""
    engine = selected_engine()
    if engine == "harness":
        from app.modules.harness.client import runtime_health as inspect
        return inspect()
    from app.modules.openwebui.supervisor import backend_ready
    ready = backend_ready()
    return {"engine": engine, "ready": ready, "status": "ready" if ready else "unavailable",
            "error_code": None if ready else "openwebui_unavailable"}


def ensure_runtime():
    """旧引擎保持现有启动方式，新引擎在对话时按会话启动隔离进程。"""
    if selected_engine() == "openwebui":
        from app.modules.openwebui.supervisor import ensure_backend
        return ensure_backend()
    health = runtime_health()
    if not health["ready"]:
        raise RuntimeError("Harness 运行文件未就绪，请先完成本地构建")
    return None


def archive_chat(db, workspace_id, user_id, conversation_id):
    """平台归档控制可见性，已有旧引擎副本继续按原归档接口隐藏。

    归档后清理该会话已空闲的 harness 目录（评审 R20）：仅当没有活跃原生进程时
    才删除，避免误删正在使用的会话 JSONL；旧引擎副本仍走原归档接口。
    """
    import shutil
    from pathlib import Path
    from types import SimpleNamespace
    from app.modules.openwebui.runtime import native_binding, archive_chat as archive_legacy
    probe = SimpleNamespace(workspace_id=workspace_id, started_by_user_id=user_id, conversation_id=conversation_id)
    if native_binding(db, probe):
        archive_legacy(db, workspace_id, user_id, conversation_id)
    from app.modules.harness.runtime import session_identity
    from app.modules.harness.client import active_session
    identity = session_identity(workspace_id, user_id, conversation_id)
    if not active_session(identity):
        home = Path(settings.HARNESS_HOME_ROOT) / "sessions" / identity
        try:
            if home.is_dir():
                shutil.rmtree(home, ignore_errors=True)
        except (OSError, ValueError):
            pass
