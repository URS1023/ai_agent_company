"""Offline checks for Bot action selection and embedding profile routing.

These tests intentionally use only pure helpers and monkeypatched provider
responses.  They do not create an application, open a database session, call
an HTTP endpoint, contact an embedding/chat provider, or execute a workflow or
tool.  Run from the repository root with::

    .\\.venv_py312\\Scripts\\python.exe -m pytest -q tests/offline/test_knowledge_bot_actions.py

中文说明：Bot 动作选择与嵌入模型档位路由的离线检查；只用纯函数与 monkeypatch 的提供方响应。
隔离方式：不创建应用、不开数据库会话、不请求 HTTP、不联系嵌入/对话提供方、不执行工作流或工具。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.errors import ModelError, ValidationError  # noqa: E402
from app.modules.knowledge_graph import bots  # noqa: E402
from app.providers import model  # noqa: E402
from app.providers.tool.base import ToolSpec  # noqa: E402


def _catalog(*, binding_id: str = "tool-1"):
    return [
        {
            "kind": "tool",
            "name": "lookup_asset",
            "display_name": "lookup_asset",
            "binding": {"id": binding_id},
            "spec": ToolSpec(
                name="lookup_asset",
                description="查询一个资产",
                provider="builtin",
                input_schema={
                    "type": "object",
                    "properties": {"asset_id": {"type": "string"}},
                    "required": ["asset_id"],
                    "additionalProperties": False,
                },
            ),
        }
    ], []


def test_bound_actions_are_not_executed_when_model_returns_no_function_call(monkeypatch):
    """An ordinary question leaves every bound capability untouched."""
    monkeypatch.setattr(bots, "_bound_action_catalog", lambda *args, **kwargs: _catalog())
    calls = []

    def no_action(*args, **kwargs):
        calls.append(kwargs)
        return {"content": "普通知识问答", "tool_calls": []}

    monkeypatch.setattr(bots.model, "chat_for_run", no_action)
    feedback = []
    selected = bots._select_bound_actions(
        None,
        bot=SimpleNamespace(workspace_id="workspace-1"),
        bindings={"tools": [{"id": "tool-1"}]},
        query="资产的定义是什么？",
        configuration={"modelProfile": "chat"},
        actor_user_id="user-1",
        runtime_run_id="run-1",
        feedback=feedback,
    )

    assert selected == []
    assert feedback == ["普通知识问答"]
    assert len(calls) == 1
    assert calls[0]["tools"][0]["function"]["name"] == "lookup_asset"


def test_bound_actions_forward_only_model_selected_json_arguments(monkeypatch):
    """A function call is the only route that produces an executable action."""
    monkeypatch.setattr(bots, "_bound_action_catalog", lambda *args, **kwargs: _catalog())

    def choose_action(*args, **kwargs):
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "lookup_asset",
                        "arguments": '{"asset_id":"asset-7"}',
                    },
                }
            ],
        }

    monkeypatch.setattr(bots.model, "chat_for_run", choose_action)
    selected = bots._select_bound_actions(
        None,
        bot=SimpleNamespace(workspace_id="workspace-1"),
        bindings={"tools": [{"id": "tool-1"}]},
        query="查询资产 asset-7",
        configuration={"modelProfile": "chat"},
        actor_user_id="user-1",
        runtime_run_id="run-1",
    )

    assert selected == [
        {
            "name": "lookup_asset",
            "kind": "tool",
            "binding": {"id": "tool-1"},
            "arguments": {"asset_id": "asset-7"},
            "model_call_id": "call-1",
        }
    ]


def test_workflow_message_mapping_rejects_ambiguous_required_inputs():
    snapshot = {
        "graph": {
            "nodes": [
                {
                    "kind": "start",
                    "config": {
                        "variables": [
                            {"variable": "asset_id", "required": True},
                            {"variable": "owner_id", "required": True},
                        ]
                    },
                }
            ]
        }
    }

    with pytest.raises(ValidationError, match="多个必填"):
        bots._workflow_inputs(snapshot, "请执行")


def test_embedding_profile_must_be_embedding_capable_and_reaches_provider_config(monkeypatch):
    profiles = {
        "tenant-embedding": {
            "source": "database",
            "base_url": "https://embedding.invalid/v1",
            "api_key": "offline-placeholder",
            "model": "tenant-embed-v2",
            "capabilities": {"capabilities": ["embedding"]},
        },
        "chat": {
            "source": "database",
            "base_url": "https://chat.invalid/v1",
            "api_key": "offline-placeholder",
            "model": "chat-v1",
            "capabilities": {"capabilities": ["chat"]},
        },
    }
    monkeypatch.setattr(model, "_profile_config", lambda name, workspace_id=None: profiles[name])

    resolved_name, config = model._embedding_config("tenant-embedding", "workspace-1")
    assert resolved_name == "tenant-embedding"
    assert config["model"] == "tenant-embed-v2"

    with pytest.raises(ModelError, match="不支持向量嵌入"):
        model._embedding_config("chat", "workspace-1")


def test_bot_retrieval_rebinds_knowledge_scope_and_excludes_withdrawn_sources(monkeypatch):
    statements, bindings = [], []
    def scalars(query):
        statements.append(query)
        return SimpleNamespace(all=lambda: ["kb1"] if len(statements) == 1 else [])
    db = SimpleNamespace(scalars=scalars, get=lambda *args: SimpleNamespace(id="kb1"))
    ctx = SimpleNamespace()
    monkeypatch.setattr(bots, "bind_data_scope", lambda _ctx, action: bindings.append(action))
    monkeypatch.setattr(bots, "require_permission", lambda *args: None)
    monkeypatch.setattr(bots, "require_resource", lambda *args: None)
    assert bots._knowledge_references(db, "ws", ["kb1"], "设备", auth_ctx=ctx) == []
    assert bindings == ["knowledge.view"]
    sql = str(statements[-1].compile(compile_kwargs={"literal_binds": True}))
    assert "files.deleted_at IS NULL" in sql
    assert "files.status = 'ready'" in sql
    assert "knowledge_bases.status = 'active'" in sql
    assert "knowledge_chunk_metadata.enabled IS true" in sql


def test_document_membership_is_distinct_and_independent_of_document_page(monkeypatch):
    from app.modules.knowledge import service
    statements = []
    def execute(query):
        statements.append(query)
        return SimpleNamespace(all=lambda: [])
    db = SimpleNamespace(
        scalar=lambda query: 201, execute=execute,
        scalars=lambda query: statements.append(query) or SimpleNamespace(all=lambda: ["file1", "file2"]),
    )
    monkeypatch.setattr(service, "get_kb", lambda *args: None)
    monkeypatch.setattr(service, "_effective_model_names", lambda *args: ("embed", "vision"))
    result = service.list_documents(db, workspace_id="ws", kb_id="kb1", file_ids=["file1", "file2"], page_size=50)
    assert result["items"] == []
    assert result["total"] == 201
    assert result["existing_file_ids"] == ["file1", "file2"]
    sql = str(statements[-1].compile(compile_kwargs={"literal_binds": True}))
    assert "SELECT DISTINCT knowledge_documents.file_id" in sql
    assert "LIMIT" not in sql
    assert "'file1', 'file2'" in sql
    with pytest.raises(ValidationError, match="500"):
        service.list_documents(db, workspace_id="ws", kb_id="kb1", file_ids=[str(i) for i in range(501)])
