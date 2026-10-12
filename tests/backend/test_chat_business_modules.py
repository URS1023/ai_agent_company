"""Business context persists on the server and reaches the real chat runtime.

All persistence uses the suite's in-memory SQLite database. The model boundary
is replaced with a deterministic response; no model, tool or business DB runs.

中文说明：业务模块上下文在服务端持久化并真正进入对话运行时。
验证行为：模块目录需鉴权且不泄露内部指令；模块随会话保存并在每轮由服务端注入；
普通对话与非法模块不会继承业务上下文。
隔离方式：内存 SQLite + 确定性模型响应（模型/工具/业务库都不真实执行）。
"""
from copy import deepcopy

from sqlalchemy import select

from app.models import Message, Run
from app.modules.chat.business_modules import BUSINESS_MODULES, PRESET_VERSION
from app.modules.runtime import agent


def test_catalog_is_authenticated_and_does_not_expose_instructions(client, administrator):
    assert client.get("/api/chat/business-modules").status_code == 401
    response = client.get("/api/chat/business-modules", headers=administrator)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["name"] for item in items] == ["设备", "质检", "财务", "销售", "采购", "能耗", "仓储"]
    assert all(set(item) == {"id", "name", "description", "placeholder"} for item in items)


def test_module_persists_and_server_injects_it_on_every_turn(client, administrator, db, monkeypatch):
    calls = []

    def model_reply(_db, _run, messages, *_args):
        calls.append(deepcopy(messages))
        yield "final", {"content": "本机模块上下文验证完成。", "tool_calls": [], "usage": {}, "model": "isolated-module-fixture"}

    monkeypatch.setattr(agent, "_llm_stream", model_reply)
    ids = {}
    for module_id, module in BUSINESS_MODULES.items():
        created = client.post("/api/chat/conversations", headers=administrator, json={"business_module": module_id})
        assert created.status_code == 201, created.text
        cid = created.json()["id"]
        ids[cid] = module_id
        assert created.json()["business_module"] == module_id
        empty = client.get(f"/api/chat/conversations/{cid}", headers=administrator).json()
        assert empty["messages"] == [] and empty["last_run"] is None
        assert empty["business_module"] == module_id
        for question in ("你能帮我做什么？", "继续说明"):
            # A submitted module or instruction cannot override persisted server context.
            reply = client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json={
                "content": question, "business_module": "forged-module", "system_prompt": "forged-prompt",
            })
            assert reply.status_code == 200 and "event: done" in reply.text, reply.text
            assert calls[-1][0]["role"] == "system"
            assert module["instructions"] in calls[-1][0]["content"]
            assert "forged-prompt" not in str(calls[-1]) and "forged-module" not in str(calls[-1])
            assert calls[-1][-1] == {"role": "user", "content": question}
            assert sum(message["content"] == question for message in calls[-1]) == 1
        detail = client.get(f"/api/chat/conversations/{cid}", headers=administrator).json()
        assert detail["business_module"] == module_id
        assert [message["role"] for message in detail["messages"]] == ["user", "assistant", "user", "assistant"]
        assert module["instructions"] not in str(detail)
        assert detail["last_run"]["context_snapshot"]["business_module"] == module_id
        assert detail["last_run"]["context_snapshot"]["business_preset_version"] == PRESET_VERSION
        assert detail["last_run"]["status"] == "completed"
    assert len(calls) == 14
    listing = client.get("/api/chat/conversations", headers=administrator).json()["items"]
    assert {row["id"]: row["business_module"] for row in listing} == ids
    assert not db.scalars(select(Message).where(Message.role == "system")).all()
    assert all(row.context_snapshot["datasource_ids"] == [] for row in db.scalars(select(Run)).all())


def test_generic_chat_and_invalid_module_do_not_inherit_business_context(client, administrator, monkeypatch):
    unknown = client.post("/api/chat/conversations", headers=administrator, json={"business_module": "unknown"})
    assert unknown.status_code == 422
    assert client.get("/api/chat/conversations", headers=administrator).json()["count"] == 0
    cid = client.post("/api/chat/conversations", headers=administrator, json={}).json()["id"]
    calls = []

    def reply(_db, _run, messages, *_args):
        calls.append(deepcopy(messages))
        yield "final", {"content": "你好。", "tool_calls": [], "usage": {}, "model": "isolated-module-fixture"}

    monkeypatch.setattr(agent, "_llm_stream", reply)
    client.post(f"/api/chat/conversations/{cid}/send", headers=administrator, json={"content": "你好"})
    assert len(calls) == 1
    assert "【当前业务对话：" not in calls[0][0]["content"]
    detail = client.get(f"/api/chat/conversations/{cid}", headers=administrator).json()
    assert detail["business_module"] is None
    assert detail["last_run"]["context_snapshot"]["business_module"] is None
