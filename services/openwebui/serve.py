"""启动完整 Open WebUI 后端，隔离依赖、数据和内部身份入口。"""
from __future__ import annotations

import hmac
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]


def configure() -> str:
    """只使用独立 SQLite，避免继承业务数据库连接串。"""
    data = Path(os.environ.get("OPENWEBUI_DATA_DIR", ROOT / "storage/openwebui")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    (data / "static").mkdir(exist_ok=True)
    key_path = data / "bridge.key"
    try:
        with key_path.open("x", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    key = key_path.read_text(encoding="utf-8").strip()
    if len(key) < 32:
        raise RuntimeError("Open WebUI 内部密钥无效")
    os.environ.update({
        "DATA_DIR": str(data), "STATIC_DIR": str(data / "static"), "DATABASE_URL": "sqlite:///" + (data / "webui.db").as_posix(),
        "WEBUI_SECRET_KEY": key, "WEBUI_AUTH": "True", "ENABLE_SIGNUP": "False",
        "WEBUI_AUTH_TRUSTED_EMAIL_HEADER": "X-Enterprise-Email",
        "WEBUI_AUTH_TRUSTED_ROLE_HEADER": "X-Enterprise-Role",
        "DEFAULT_USER_ROLE": "user", "ENABLE_DIRECT_CONNECTIONS": "True",
        "ENABLE_PERSISTENT_CONFIG": "False",
        "USER_PERMISSIONS_WORKSPACE_SKILLS_ACCESS": "True",
        "USER_PERMISSIONS_FEATURES_DIRECT_TOOL_SERVERS": "True",
        "OFFLINE_MODE": "True", "ENABLE_VERSION_UPDATE_CHECK": "False",
        "RAG_EMBEDDING_ENGINE": "openai", "RAG_EMBEDDING_MODEL_AUTO_UPDATE": "False",
        "ENABLE_OLLAMA_API": "False", "ENABLE_OPENAI_API": "False",
        "LOGURU_DIAGNOSE": "False", "DO_NOT_TRACK": "True", "ANONYMIZED_TELEMETRY": "False",
        "WEBUI_SESSION_COOKIE_SECURE": "False",
        # 空值由上游解析为 None，长任务只由取消事件结束，不被 24 小时调用器时限截断。
        "WEBSOCKET_EVENT_CALLER_TIMEOUT": "",
        # 复杂业务交付由平台自己的任务核验收口；原生工具轮次不因固定次数提前结束。
        # 用户仍可通过停止按钮取消，模型/工具调用的取消与权限边界保持不变。
        "CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS": "-1",
    })
    # 此进程不接收业务库、外部缓存及模型供应商凭据。
    for name in ("REDIS_URL", "DATABASE_HOST", "DATABASE_PASSWORD", "DATABASE_USER",
                 "DATABASE_NAME", "OPENAI_API_KEY", "OPENAI_API_BASE_URL", "WEBUI_ADMIN_PASSWORD"):
        os.environ.pop(name, None)
    sys.path.insert(0, str(Path(__file__).parent / "upstream/backend"))
    return key


class InternalAccess:
    """内部 HTTP 和 WebSocket 均要求共享密钥，拒绝伪造可信身份头。"""

    def __init__(self, app, key: str):
        self.app, self.key = app, key

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"x-enterprise-bridge-key", b"").decode("ascii", errors="ignore")
            if not hmac.compare_digest(supplied, self.key):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send({"type": "http.response.start", "status": 403, "headers": []})
                    await send({"type": "http.response.body", "body": b"Forbidden"})
                return
        if scope["type"] == "lifespan":
            async def lifecycle_send(message):
                """上游配置日志完成后再增加文件接收器，避免被其初始化覆盖。"""
                if message["type"] == "lifespan.startup.complete":
                    from loguru import logger
                    logger.add(Path(os.environ["DATA_DIR"]) / "logs/runtime.jsonl",
                               rotation="20 MB", retention=5, serialize=True,
                               diagnose=False, backtrace=False, encoding="utf-8")
                await send(message)
            await self.app(scope, receive, lifecycle_send)
        else:
            await self.app(scope, receive, send)


if __name__ == "__main__":
    key = configure()
    logs = Path(os.environ["DATA_DIR"]) / "logs"
    logs.mkdir(exist_ok=True)
    handler = RotatingFileHandler(logs / "backend.log", maxBytes=20_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(handler)
    import uvicorn
    from open_webui.main import app

    # 上游会清理 STATIC_DIR；只在独立运行目录恢复原生资源，源码始终保持只读。
    shutil.copytree(Path(__file__).parent / "upstream/backend/open_webui/static",
                    Path(os.environ["STATIC_DIR"]), dirs_exist_ok=True)
    uvicorn.run(InternalAccess(app, key), host="127.0.0.1",
                port=int(os.environ.get("OPENWEBUI_PORT", "8012")), access_log=False)
