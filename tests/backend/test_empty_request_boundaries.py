"""Reject empty requests before writing runs or contacting models/databases.

中文说明：空请求必须在写运行记录、调用模型或连库之前被拒绝：空白对话、仅附件请求需明确指令、
组件查询非法参数、空白查询体都在服务层前返回错误。
隔离方式：client 夹具（内存 SQLite + TestClient），断言"未产生写入"。
"""
import pytest
from sqlalchemy import select

from app.models import Message, Run
from app.modules.analytics import queries
from app.modules.runtime import chat_jobs, engine
from app.modules.runtime.service import create_message, finalize_run


@pytest.mark.parametrize("content", ["", " \t\r\n", "\u3000"])
def test_blank_chat_rejected_without_message_or_job(client, administrator, db, monkeypatch, content):
    attempts = []
    def unexpected(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Empty chat reached the executor")
    monkeypatch.setattr(chat_jobs, "start_job", unexpected)
    conversation = client.post("/api/chat/conversations", headers=administrator, json={"title": "空输入边界"}).json()
    response = client.post(f"/api/chat/conversations/{conversation['id']}/send", headers=administrator, json={"content": content})
    assert response.status_code == 422, response.text
    assert attempts == []
    assert db.scalars(select(Message).where(Message.conversation_id == conversation["id"])).all() == []
    assert db.scalars(select(Run).where(Run.conversation_id == conversation["id"])).all() == []


@pytest.mark.parametrize("content", ["", " \n "])
def test_attachment_only_request_gets_explicit_instruction(client, administrator, db, monkeypatch, content):
    def respond(run_db, run, workspace, user, **kwargs):
        # 当前测试固定走 Open WebUI 引擎，只验证附件空正文的补全，不调用真实模型。
        create_message(run_db, workspace_id=run.workspace_id, conversation_id=run.conversation_id,
                       role="assistant", content="附件内容已读取。")
        finalize_run(run_db, run, status="completed")
        run_db.commit()
        yield {"type": "run.completed", "payload": {"status": "completed"}}
    monkeypatch.setattr(engine, "run_chat", respond)
    uploaded = client.post("/api/files", headers=administrator,
                           files={"file": ("input-boundary.txt", b"bounded attachment", "text/plain")})
    assert uploaded.status_code == 201, uploaded.text
    conversation = client.post("/api/chat/conversations", headers=administrator, json={"title": "仅附件"}).json()
    response = client.post(f"/api/chat/conversations/{conversation['id']}/send", headers=administrator,
                           json={"content": content, "file_ids": [uploaded.json()["id"]]})
    assert response.status_code == 200 and "run.completed" in response.text, response.text
    message = db.scalar(select(Message).where(Message.conversation_id == conversation["id"], Message.role == "user"))
    assert message.content == "请分析我上传的文件。"
    run = db.scalar(select(Run).where(Run.conversation_id == conversation["id"]))
    assert run.context_snapshot["user_request"] == message.content


@pytest.mark.parametrize("parameters", [
    {"datasourceId": "fixture", "sql": ""},
    {"datasourceId": "fixture", "sql": " \n\t"},
    {"datasourceId": "", "sql": "SELECT 1"},
    {"datasourceId": "x" * 27, "sql": "SELECT 1"},
])
def test_component_query_rejects_invalid_parameters_before_service(client, administrator, monkeypatch, parameters):
    attempts = []
    def unexpected(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Invalid component request reached SQL service")
    monkeypatch.setattr(queries, "execute_query", unexpected)
    response = client.post("/api/v1/analytics/execute-sql", headers=administrator, params=parameters)
    assert response.status_code == 422, response.text
    assert attempts == []


@pytest.mark.parametrize("mode", ["sql", "natural_language"])
def test_whitespace_query_body_rejected_before_service(client, administrator, monkeypatch, mode):
    attempts = []
    def unexpected(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Blank query reached SQL/model service")
    monkeypatch.setattr(queries, "execute_query", unexpected)
    response = client.post("/api/v1/analytics/query", headers=administrator,
                           json={"datasourceId": "fixture", "query": " \n\t", "mode": mode})
    assert response.status_code == 422, response.text
    assert attempts == []
