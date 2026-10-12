"""Isolated SQLite tests: never initialize or drop the user's MySQL database.

中文说明：backend 测试的公共夹具（FastAPI TestClient + 内存 SQLite 会话 + 管理员/权限/报告语义夹具），
所有用例都跑在这套隔离环境里，不会初始化或删改用户本机的 MySQL 库。
运行方式：pytest tests/backend（或经 tests/run_isolated_backend.py 统一入口执行）。
边界：外部数据库驱动被禁用；真实 MySQL/模型/浏览器验收属于另一套独立 gate。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["APP_ENV"] = "test"
os.environ["AUTO_MIGRATE"] = "1"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "IsolatedBootstrap12345"
# 测试确定性：不继承 .env.local 的 CHAT_ENGINE=harness 部署开关；非 harness 用例
# 打补丁的引擎是 openwebui 运行时，统一钉住避免测试被改派到需要真实进程的链路。
os.environ["CHAT_ENGINE"] = "openwebui"
# Most unit tests build the exact records they exercise.  The dedicated report
# import tests invoke the production importer directly so every unrelated test
# does not have to insert 39 large dashboard documents during fixture startup.
os.environ["BOOTSTRAP_LYNX_REPORTS"] = "0"
# 隔离 storage：直接跑 pytest tests/backend 时，产物/文件也进一次性临时目录，
# 绝不写入仓库真实 storage/（评审 R24；run_isolated_backend.py 已做同样隔离）。
os.environ.setdefault("LOCAL_STORAGE_ROOT", str(Path(tempfile.mkdtemp(prefix="test5-backend-storage-"))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from fastapi.testclient import TestClient
from app.db import get_engine, new_session
from app.main import create_app
from app.models import Base


@pytest.fixture(autouse=True)
def isolated_semantic_embeddings(monkeypatch):
    """Metadata fixtures must not contact a configured external embedding model.

    Tests that exercise embeddings can install their own deterministic response.
    """
    from app.providers import model
    monkeypatch.setattr(model, "embed_texts", lambda texts, **kwargs: [[] for _ in texts])


@pytest.fixture
def client():
    engine = get_engine()
    assert engine.url.get_backend_name() == "sqlite" and engine.url.database == ":memory:"
    Base.metadata.drop_all(engine)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    Base.metadata.drop_all(engine)


@pytest.fixture
def administrator(client):
    response = client.post("/api/auth/login", json={"username": "workspace-admin", "password": os.environ["BOOTSTRAP_ADMIN_PASSWORD"]})
    assert response.status_code == 200, response.text
    data = response.json()
    return {"Authorization": "Bearer " + data["token"], "X-Workspace-Id": data["workspaces"][0]["id"]}


@pytest.fixture
def db(client):
    with new_session() as session:
        yield session


@pytest.fixture
def approved_report_semantics(monkeypatch):
    """Isolate existing layout/query tests from the separately tested AI review."""
    from app.modules.reports import generation

    def approve(ctx, requirement, requirement_document, schemas, widgets, title, *args, **kwargs):
        return {"aligned": True, "reason": "isolated semantic review fixture"}, {"mode": "semantic_review"}

    monkeypatch.setattr(generation, "_review_semantics", approve)
