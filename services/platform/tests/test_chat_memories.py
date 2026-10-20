"""仅新临时SQLite与进程内ASGI验证个人记忆；不调用真实服务或模型。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "services/platform") not in sys.path:
    sys.path.insert(0, str(ROOT / "services/platform"))

from agent_platform.core.context import Actor, get_actor
from agent_platform.core.database import Base, get_session
from agent_platform.core.errors import DomainError
from agent_platform.core.models import AuditEvent, Organization, User, Workspace
from agent_platform.modules.chat.memories import MemoryCreate, MemoryPatch, MemoryService
from agent_platform.modules.chat.memory_models import UserMemory
from agent_platform.modules.chat.router import router

TABLES = [Organization.__table__, Workspace.__table__, User.__table__, AuditEvent.__table__, UserMemory.__table__]


@pytest.fixture
def environment(tmp_path):
    engine = sa.create_engine("sqlite:///" + (tmp_path / "new-private-memories.sqlite3").as_posix(), connect_args={"check_same_thread": False})
    @sa.event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Organization(id="org", name="新临时组织"))
        session.flush()
        session.add_all([Workspace(id=name, organization_id="org", name=name) for name in ("workspace-a", "workspace-b")])
        session.add_all([User(id=name, organization_id="org", username=name, display_name=name, password_hash="fixture-only", is_admin=name == "admin") for name in ("first", "second", "admin")])
        session.commit()
        actor = Actor("first", "workspace-a", frozenset({"m02.read", "m02.write", "m02.delete"}))
        yield session, actor, engine
    engine.dispose()


def test_crud_search_type_pagination_and_no_automatic_collection(environment):
    session, actor, _ = environment
    service = MemoryService(session, actor)
    assert service.list() == {"items": [], "total": 0}
    first = service.create(MemoryCreate(content="  优先关注包装车间  "))
    second = service.create(MemoryCreate(content="出厂报告使用中文", type="context"))
    third = service.create(MemoryCreate(content="查询标签包含 10%_A"))
    assert first["content"] == "优先关注包装车间" and first["type"] == "user"
    assert service.get(first["id"]) == first
    assert service.list("包装")["items"] == [first]
    assert service.list("10%_A")["items"] == [third]
    assert service.list("%_")["total"] == 1
    assert service.list(type="context")["items"] == [second]
    page = service.list(limit=1)
    next_page = service.list(limit=1, offset=1)
    assert page["total"] == next_page["total"] == 3
    assert page["items"][0]["id"] != next_page["items"][0]["id"]
    assert service.list(limit=1, offset=50) == {"items": [], "total": 3}
    updated = service.update(first["id"], MemoryPatch(revision=1, content="仅分析本班次", type="context"))
    assert updated["revision"] == 2 and updated["content"] == "仅分析本班次"
    assert service.list("包装") == {"items": [], "total": 0}


@pytest.mark.parametrize("subject", [
    Actor("second", "workspace-a", frozenset({"*"})),
    Actor("first", "workspace-b", frozenset({"*"})),
    Actor("admin", "workspace-a", frozenset({"*"}), is_admin=True),
])
def test_owner_workspace_and_admin_are_isolated_before_pagination(environment, subject):
    session, actor, _ = environment
    original = MemoryService(session, actor).create(MemoryCreate(content="私人工作背景"))
    other = MemoryService(session, subject)
    assert other.list(q="私人", limit=1) == {"items": [], "total": 0}
    own = other.create(MemoryCreate(content="自己的条目"))
    assert other.list()["total"] == 1 and other.list()["items"] == [own]
    for operation in (lambda: other.get(original["id"]),
                      lambda: other.update(original["id"], MemoryPatch(revision=1, content="越权")),
                      lambda: other.delete(original["id"], 1)):
        with pytest.raises(DomainError) as missing:
            operation()
        assert missing.value.code == "memory_not_found" and missing.value.status == 404
    assert MemoryService(session, actor).get(original["id"])["content"] == "私人工作背景"


def test_stale_revision_cannot_update_or_delete(environment):
    session, actor, engine = environment
    service = MemoryService(session, actor)
    original = service.create(MemoryCreate(content="旧内容"))
    session.commit()
    with Session(engine) as other_session:
        changed = MemoryService(other_session, actor).update(original["id"], MemoryPatch(revision=1, content="新内容"))
        other_session.commit()
    for operation in (lambda: service.update(original["id"], MemoryPatch(revision=1, content="覆盖")),
                      lambda: service.delete(original["id"], 1)):
        with pytest.raises(DomainError) as conflict:
            operation()
        assert conflict.value.status == 409 and conflict.value.code == "revision_conflict"
    session.expire_all()
    assert service.get(original["id"]) == changed


def test_delete_removes_body_and_audits_never_copy_content(environment):
    session, actor, _ = environment
    service = MemoryService(session, actor)
    original = service.create(MemoryCreate(content="不应进入审计的私人正文"))
    updated = service.update(original["id"], MemoryPatch(revision=1, content="更新后的私人正文"))
    assert service.delete(original["id"], updated["revision"]) == {"id": original["id"], "deleted": True}
    assert session.scalar(sa.select(sa.func.count()).select_from(UserMemory)) == 0
    assert service.list() == {"items": [], "total": 0}
    with pytest.raises(DomainError) as missing:
        service.get(original["id"])
    assert missing.value.status == 404
    events = list(session.scalars(sa.select(AuditEvent).where(AuditEvent.resource_type == "user_memory")))
    assert [event.action for event in events] == ["create", "update", "delete"]
    assert all("正文" not in str(event.details) for event in events)


@pytest.mark.parametrize("permission,method", [
    ("m02.write", "list"), ("m02.write", "get"), ("m02.read", "create"),
    ("m02.read", "update"), ("m02.read", "delete"),
])
def test_indirect_service_calls_require_operation_permission(environment, permission, method):
    session, actor, _ = environment
    original = MemoryService(session, actor).create(MemoryCreate(content="个人记忆"))
    limited = MemoryService(session, Actor(actor.user_id, actor.workspace_id, frozenset({permission})))
    arguments = {"list": (), "get": (original["id"],), "create": (MemoryCreate(content="条目"),),
                 "update": (original["id"], MemoryPatch(revision=1, content="条目")), "delete": (original["id"], 1)}
    with pytest.raises(DomainError) as denied:
        getattr(limited, method)(*arguments[method])
    assert denied.value.status == 403


@pytest.mark.parametrize("data", [{"content": "   "}, {"content": "x" * 6001},
    {"content": "a\0b"}, {"content": "正文", "user_id": "other"}, {"content": "正文", "type": "shared"}])
def test_create_rejects_invalid_content_and_client_owned_fields(data):
    with pytest.raises(ValidationError):
        MemoryCreate.model_validate(data)


@pytest.mark.parametrize("data", [{"revision": 1}, {"revision": 1, "content": None},
    {"revision": 1, "type": None}, {"revision": 1, "workspace_id": "other"},
    {"revision": 0, "content": "正文"}])
def test_patch_requires_valid_real_change(data):
    with pytest.raises(ValidationError):
        MemoryPatch.model_validate(data)


def test_in_process_routes_keep_frozen_contract_and_reject_forged_scope(environment):
    session, actor, _ = environment
    subject = {"actor": actor}
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_actor] = lambda: subject["actor"]
    app.dependency_overrides[get_session] = lambda: session
    @app.exception_handler(DomainError)
    async def error(_request, exc):
        return JSONResponse({"error": {"code": exc.code}}, status_code=exc.status)
    with TestClient(app) as client:
        base = "/api/v1/chat/memories"
        response = client.post(base, json={"content": "只使用中文", "type": "user"})
        assert response.status_code == 201
        row = response.json()
        assert client.get(base, params={"q": "中文", "limit": 1}).json() == {"items": [row], "total": 1}
        assert client.patch(base + "/" + row["id"], json={"revision": 1, "content": "优先当前产线"}).json()["revision"] == 2
        assert client.delete(base + "/" + row["id"], params={"revision": 1}).status_code == 409
        assert client.post(base, json={"content": "越权", "workspace_id": "workspace-b"}).status_code == 422
        assert client.get(base, params={"limit": 101}).status_code == 422
        assert client.get(base, params={"q": "x" * 201}).status_code == 422
        subject["actor"] = Actor("second", "workspace-a", frozenset({"*"}))
        assert client.get(base).json() == {"items": [], "total": 0}
        assert client.get(base + "/" + row["id"]).status_code == 404
        subject["actor"] = actor
        assert client.delete(base + "/" + row["id"], params={"revision": 2}).json() == {"id": row["id"], "deleted": True}


def migration():
    spec = importlib.util.spec_from_file_location("memory_migration09", ROOT / "services/platform/migrations/versions/20261021_09_个人记忆.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_matches_model_is_idempotent_and_preserves_entries(environment):
    session, actor, engine = environment
    row = MemoryService(session, actor).create(MemoryCreate(content="迁移中保留的条目"))
    session.commit()
    module = migration()
    expected = sa.MetaData()
    for table in TABLES:
        table.to_metadata(expected)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()
        assert compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), expected) == []
        assert connection.execute(sa.text("SELECT content FROM chat_user_memories WHERE id=:id"), {"id": row["id"]}).scalar_one() == row["content"]


def test_migration_creates_only_memory_table_on_fresh_parent_schema(tmp_path):
    engine = sa.create_engine("sqlite:///" + (tmp_path / "new-migration09.sqlite3").as_posix())
    try:
        Base.metadata.create_all(engine, tables=[Organization.__table__, Workspace.__table__, User.__table__])
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration().upgrade()
            assert set(sa.inspect(connection).get_table_names()) == {"organizations", "workspaces", "users", "chat_user_memories"}
    finally:
        engine.dispose()


@pytest.mark.parametrize("drift", ["column", "index"])
def test_migration_refuses_unknown_same_name_schema_without_data_changes(environment, drift):
    session, actor, engine = environment
    row = MemoryService(session, actor).create(MemoryCreate(content="不可覆盖的原条目"))
    session.commit()
    with engine.begin() as connection:
        if drift == "column":
            connection.execute(sa.text("ALTER TABLE chat_user_memories ADD COLUMN unexpected TEXT"))
        else:
            connection.execute(sa.text("DROP INDEX ix_chat_user_memories_scope"))
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="memory_migration_"):
                migration().upgrade()
        assert connection.execute(sa.text("SELECT content FROM chat_user_memories WHERE id=:id"), {"id": row["id"]}).scalar_one() == row["content"]
