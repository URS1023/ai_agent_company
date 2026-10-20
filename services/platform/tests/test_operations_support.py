"""系统辅助配置的隔离、版本与字段校验；只使用每条测试新建的本机临时SQLite。"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_platform.core.context import Actor
from agent_platform.core.database import Database
from agent_platform.core.errors import DomainError
from agent_platform.core.models import AuditEvent, Organization, User, Workspace
from agent_platform.core.settings import Settings
from agent_platform.main import create_app
from agent_platform.modules.operations.support_models import OperationSupportRevision
from agent_platform.modules.operations.support_schemas import SupportInput
from agent_platform.modules.operations.support_service import SupportService
from agent_platform.modules.operations.support_validation import validate_definition


@pytest.fixture
def services(tmp_path):
    database = Database(Settings(data_dir=tmp_path, environment="test"))
    assert Path(database.engine.url.database).resolve().is_relative_to(tmp_path.resolve())
    database.initialize()
    with database.sessions() as session:
        session.add(Organization(id="org", name="辅助配置隔离测试企业"))
        session.flush()
        session.add_all([Workspace(id="ws", organization_id="org", name="甲车间"), Workspace(id="other", organization_id="org", name="乙车间")])
        session.add_all([User(id=name, organization_id="org", username=name, display_name=name, password_hash="offline-no-login") for name in ("alice", "bob", "admin")])
        session.commit()
        actors = [Actor(user, "ws", frozenset({"m16.read", "m16.admin"}), organization_id="org") for user in ("alice", "bob")]
        actors += [Actor("admin", "ws", frozenset({"*"}), organization_id="org", is_admin=True), Actor("alice", "other", frozenset({"*"}), organization_id="org")]
        yield session, [SupportService(session, actor) for actor in actors]
    database.dispose()


def create(service, kind="dictionary", **changes):
    data = {"name": "设备状态", "code": "equipment.status", "definition": {"entries": [{"label": "运行", "value": "running"}]} if kind == "dictionary" else {"value_type": "number", "value": 12}}
    data.update(changes)
    return service.create(kind, SupportInput(**data).model_dump())


def assert_error(status, action):
    with pytest.raises(DomainError) as caught:
        action()
    assert caught.value.status == status
    return caught.value


def test_scope_isolation_personal_override_and_disabled_fallback(services):
    _, (alice, bob, admin, other) = services
    shared = create(alice)
    personal = create(alice, scope="personal", definition={"entries": [{"label": "个人运行", "value": "own"}]})
    assert alice.dictionary_options(shared["code"])["items"][0]["value"] == "own"
    assert bob.dictionary_options(shared["code"])["items"][0]["value"] == "running"
    assert bob.list("dictionary")["total"] == 1
    assert admin.list("dictionary")["total"] == 2
    assert other.list("dictionary")["items"] == []
    assert_error(404, lambda: bob.get("dictionary", personal["id"]))
    assert_error(404, lambda: other.get("dictionary", shared["id"]))
    alice.patch("dictionary", personal["id"], {"revision": 1, "enabled": False})
    assert alice.dictionary_options(shared["code"])["items"][0]["value"] == "running"
    assert_error(409, lambda: create(alice))
    assert create(other)["id"] != shared["id"]


def test_compare_and_swap_history_restore_soft_delete_and_audit(services):
    session, (alice, _, _, _) = services
    row = create(alice, "parameter", code="report.page_size")
    changed = alice.patch("parameter", row["id"], {"revision": 1, "name": "每页条数", "definition": {"value_type": "number", "value": 30}})
    assert changed["revision"] == 2
    assert_error(409, lambda: alice.patch("parameter", row["id"], {"revision": 1, "enabled": False}))
    restored = alice.restore("parameter", row["id"], {"revision": 2, "target_revision": 1})
    assert restored["revision"] == 3 and restored["definition"]["value"] == 12
    assert [item["number"] for item in alice.revisions("parameter", row["id"])["items"]] == [3, 2, 1]
    assert_error(422, lambda: alice.patch("parameter", row["id"], {"revision": 3, "owner_id": "bob"}))
    alice.patch("parameter", row["id"], {"revision": 3, "deleted": True})
    assert alice.list("parameter")["total"] == 0
    assert_error(404, lambda: alice.get("parameter", row["id"]))
    assert len(session.scalars(select(OperationSupportRevision).where(OperationSupportRevision.resource_id == row["id"])).all()) == 4
    events = session.scalars(select(AuditEvent).where(AuditEvent.resource_id == row["id"])).all()
    assert len(events) == 4
    assert all("definition" not in event.details and "value" not in event.details for event in events)


def test_permission_and_scope_changes_cannot_reassign_other_owner(services):
    session, (alice, bob, _, _) = services
    row = create(alice)
    readonly = SupportService(session, Actor("alice", "ws", frozenset({"m16.read"})))
    assert readonly.get("dictionary", row["id"]).name == row["name"]
    assert_error(403, lambda: create(readonly))
    assert_error(403, lambda: readonly.patch("dictionary", row["id"], {"revision": 1, "enabled": False}))
    assert_error(403, lambda: bob.patch("dictionary", row["id"], {"revision": 1, "scope": "personal"}))
    own = create(alice, scope="personal")
    assert_error(409, lambda: alice.patch("dictionary", own["id"], {"revision": 1, "scope": "workspace"}))
    assert alice.get("dictionary", own["id"]).revision == 1


def test_dictionary_normalization_default_and_consumer_visibility(services):
    session, (alice, _, _, _) = services
    row = create(alice, definition={"entries": [
        {"label": "停机", "value": "stopped", "order": 5, "enabled": False},
        {"label": "运行", "value": "running", "order": 1, "is_default": True, "tag_type": "success"},
    ]})
    assert row["definition"]["entries"][0]["value"] == "running"
    employee = SupportService(session, Actor("bob", "ws", frozenset()))
    assert len(employee.dictionary_options(row["code"])["items"]) == 1
    assert_error(403, lambda: employee.list("dictionary"))
    for entries in (
        [{"label": "甲", "value": "same"}, {"label": "乙", "value": "same"}],
        [{"label": "甲", "value": "a", "is_default": True}, {"label": "乙", "value": "b", "is_default": True}],
        [{"label": "甲", "value": "a", "is_default": True, "enabled": False}],
    ):
        assert_error(422, lambda: validate_definition("dictionary", {"entries": entries}))


@pytest.mark.parametrize("definition", [
    {"value_type": "number", "value": True}, {"value_type": "number", "value": float("nan")},
    {"value_type": "boolean", "value": "false"}, {"value_type": "json", "value": "{}"},
])
def test_parameter_types_are_not_coerced(definition):
    assert_error(422, lambda: validate_definition("parameter", definition))


def test_secret_parameters_and_unsupported_kind_rejected(services):
    _, (alice, _, _, _) = services
    assert_error(422, lambda: create(alice, "parameter", code="erp.api_key"))
    assert_error(404, lambda: create(alice, "executor"))


def test_announcement_feed_honors_dates_scope_and_enabled(services):
    _, (alice, _, _, other) = services
    now = datetime.now(timezone.utc)
    definition = {"category": "notice", "content": "今晚设备保养", "publish_at": (now - timedelta(hours=1)).isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat()}
    create(alice, "announcement", name="保养安排", code="maintenance.notice", definition=definition)
    create(alice, "announcement", code="future.notice", definition={**definition, "publish_at": (now + timedelta(minutes=10)).isoformat()})
    create(alice, "announcement", code="expired.notice", definition={**definition, "expires_at": (now - timedelta(minutes=1)).isoformat()})
    create(alice, "announcement", code="disabled.notice", enabled=False, definition=definition)
    create(alice, "announcement", code="personal.notice", scope="personal", definition=definition)
    assert [row["name"] for row in alice.announcements()["items"]] == ["保养安排"]
    assert other.announcements()["items"] == []
    assert_error(422, lambda: validate_definition("announcement", {**definition, "publish_at": "2026-10-21T12:00:00"}))
    assert_error(422, lambda: validate_definition("announcement", {**definition, "expires_at": definition["publish_at"]}))


FORM_FIELDS = [
    {"key": "description", "label": "检查记录", "kind": "textarea", "required": True, "max_length": 8},
    {"key": "count", "label": "数量", "kind": "integer", "required": True, "minimum": 1, "maximum": 5},
    {"key": "ready", "label": "已准备", "kind": "boolean", "required": True},
    {"key": "status", "label": "状态", "kind": "select", "choices": [{"label": "运行", "value": "running"}], "required": True},
    {"key": "date", "label": "日期", "kind": "date"},
]


def test_form_schema_preview_revision_and_no_input_value_in_audit(services):
    session, (alice, _, _, _) = services
    form = create(alice, "form", code="inspection.form", definition={"fields": FORM_FIELDS})
    schema = alice.schema(form["id"])
    assert schema["additionalProperties"] is False
    assert schema["properties"]["description"]["x-control"] == "textarea"
    assert schema["properties"]["status"]["x-options"] == [{"label": "运行", "value": "running"}]
    assert schema["properties"]["count"]["type"] == "integer" and schema["properties"]["count"]["minimum"] == 1
    assert schema["properties"]["date"]["format"] == "date"
    assert "date" not in schema["required"]
    good = {"description": "现场检查完成", "count": 2, "ready": False, "status": "running", "date": "2026-10-21"}
    assert alice.validate(form["id"], {"revision": 1, "values": good})["valid"] is True
    bad = alice.validate(form["id"], {"revision": 1, "values": {**good, "count": 2.1, "description": "  ", "status": "unknown", "date": "2026-02-30", "extra": 9}})
    assert set(bad["errors"]) == {"count", "description", "status", "date", "_form"}
    assert "现场检查完成" not in json.dumps([event.details for event in session.scalars(select(AuditEvent))], ensure_ascii=False)
    assert_error(409, lambda: alice.validate(form["id"], {"revision": 2, "values": good}))
    assert_error(422, lambda: alice.validate(form["id"], {"revision": 1, "values": {"count": float("inf")}}))


@pytest.mark.parametrize("fields", [
    [], [{"key": "constructor", "label": "危险键"}],
    [{"key": "a", "label": "甲"}, {"key": "a", "label": "乙"}],
    [{"key": "a", "label": "甲", "kind": "integer", "minimum": 1.5}],
    [{"key": "a", "label": "甲", "kind": "select", "choices": []}],
    [{"key": "a", "label": "甲", "kind": "number", "minimum": 5, "maximum": 1}],
])
def test_form_field_contract_rejects_unsafe_or_ambiguous_fields(fields):
    assert_error(422, lambda: validate_definition("form", {"fields": fields}))


def test_list_pagination_counts_all_current_scope_records(services):
    _, (alice, _, _, _) = services
    for index in range(3):
        create(alice, code=f"state.{index}")
    first = alice.list("dictionary", limit=1)
    second = alice.list("dictionary", limit=1, offset=1)
    assert first["total"] == second["total"] == 3
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_real_router_csrf_immutable_fields_contract_and_scaffold(tmp_path, monkeypatch):
    # ASGI进程内请求不监听端口；只创建当前tmp_path/platform.sqlite3元数据。
    monkeypatch.setenv("AGENT_MASTER_KEY", Fernet.generate_key().decode())
    app = create_app(Settings(data_dir=tmp_path, environment="test", enabled_modules=("identity", "operations")))
    with TestClient(app) as client:
        result = client.post("/api/v1/auth/setup", json={"username": "support-admin", "password": "Local-form-test-2026!", "display_name": "工具测试", "workspace_name": "新本机隔离区"})
        assert result.status_code == 201, result.text
        headers = {"X-CSRF-Token": result.json()["csrf_token"]}
        data = {"name": "现场表单 </script>", "code": "device.check", "definition": {"fields": FORM_FIELDS}}
        assert client.post("/api/v1/operations/forms", json=data).status_code == 403
        created = client.post("/api/v1/operations/forms", json=data, headers=headers)
        assert created.status_code == 201, created.text
        row = created.json()
        root = "/api/v1/operations/forms/" + row["id"]
        assert client.patch(root, json={"revision": 1, "owner_id": "different"}, headers=headers).status_code == 422
        assert client.patch(root, json={"revision": 1, "code": "changed"}, headers=headers).status_code == 422
        preview = client.post(root + "/validations", json={"revision": 1, "values": {}}, headers=headers)
        assert preview.status_code == 200 and preview.json()["valid"] is False
        schema = client.get(root + "/schema")
        assert schema.status_code == 200 and schema.json()["properties"]["count"]["maximum"] == 5
        vue = client.get(root + "/scaffold?format=vue")
        assert vue.status_code == 200 and "const schema: Record<string, any>" in vue.text
        assert "x-control" in vue.text and "x-options" in vue.text and "value-format=\"YYYY-MM-DD\"" in vue.text
        assert "现场表单 </script>" not in vue.text and r"\u003c/script>" in vue.text
        types = client.get(root + "/scaffold?format=typescript")
        assert '"ready": boolean;' in types.text and '"date"?: string;' in types.text
        catalog = client.get("/api/v1/operations/developer-tools/openapi").json()
        assert root.rsplit("/", 1)[0] + "/{id}/schema" in catalog["paths"]
        assert "/api/v1/operations/announcements/{id}/restorations" in catalog["paths"]
        assert client.get("/api/v1/operations/forms?kind=parameter").json()["items"][0]["kind"] == "form"
        assert client.delete(root + "?revision=1", headers=headers).status_code == 200
        assert client.get(root).status_code == 404


def test_definition_budget_counts_utf8_bytes_and_preview_boolean_is_strict(services):
    _, (alice, _, _, _) = services
    # 中文字符需要按UTF-8字节计量，避免100KB限制实际放入数倍内容。
    assert_error(422, lambda: validate_definition("parameter", {"value_type": "string", "value": "质" * 34000}))
    form = create(alice, "form", code="boolean.strict", definition={"fields": [{"key": "ready", "label": "准备完成", "kind": "boolean", "required": True}]})
    assert alice.validate(form["id"], {"revision": 1, "values": {"ready": 0}})["errors"] == {"ready": "请选择有效状态"}
    assert alice.validate(form["id"], {"revision": 1, "values": {"ready": False}})["valid"] is True
