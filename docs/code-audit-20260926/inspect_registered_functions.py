"""只装配应用并导出实际注册接口，禁止启动生命周期及任何数据库、网络访问。"""
from __future__ import annotations

import inspect
import json
import logging
import os
import socket
import sys
import tempfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def forbidden(*args, **kwargs):
    """静态装配检查不允许产生外部连接。"""
    raise AssertionError("功能清单装配期间禁止连接数据库或网络")


def main() -> None:
    """导出带源码行号的真实路由，不调用接口，也不进入 lifespan。"""
    sys.path.insert(0, str(ROOT / "backend"))
    with tempfile.TemporaryDirectory(prefix="test5-function-inventory-") as temporary:
        os.environ.update(DATABASE_URL="sqlite:///:memory:", LOCAL_STORAGE_ROOT=temporary,
                          LOG_DIR=str(Path(temporary) / "logs"), SCHEDULER_ENABLED="0",
                          SECRET_MASTER_KEY="audit-assembly-only")
        socket.socket.connect = forbidden
        socket.socket.connect_ex = forbidden
        socket.create_connection = forbidden
        socket.getaddrinfo = forbidden
        from sqlalchemy.engine import Engine
        Engine.connect = forbidden
        from app.main import app
        from fastapi.routing import APIRoute
        rows = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            function = inspect.unwrap(route.endpoint)
            source = Path(inspect.getsourcefile(function)).resolve()
            rows.append({"path": route.path, "methods": sorted(route.methods),
                         "summary": route.summary, "tags": route.tags,
                         "file": source.relative_to(ROOT).as_posix(),
                         "line": inspect.getsourcelines(function)[1],
                         "function": function.__name__})
        destination = Path(__file__).resolve().parent / "registered-functions.json"
        destination.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"registered_routes": len(rows),
                          "api_routes": sum(row["path"].startswith("/api/") for row in rows),
                          "page_routes": sum(not row["path"].startswith("/api/") for row in rows),
                          "modules": len(app.state.loaded_modules),
                          "routes_by_source": dict(Counter(row["file"] for row in rows))},
                         ensure_ascii=False, indent=2))
        for handler in list(logging.getLogger().handlers):
            if getattr(handler, "_execution_owned", False):
                logging.getLogger().removeHandler(handler)
                handler.close()
        logging.shutdown()


if __name__ == "__main__":
    main()
