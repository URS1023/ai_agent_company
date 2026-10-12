"""Qknow-compatible tool taxonomy and complete capability asset lifecycle.

中文说明：工具分类体系（保持兼容）与能力资产全生命周期：分类树与版本、方法元数据禁止真实执行、
批量操作原子性、方法测试复用已保存的私网授权、连接器/技能/插件的复制共享归档保留版本。
隔离方式：client 夹具（内存 SQLite + TestClient），真实落库，不连业务库、不调用外部服务。
"""
from __future__ import annotations

from sqlalchemy import select

from app.models import Connector, Plugin, Skill
from test_capabilities_center import API, create_http, http_payload, result
from test_collection import upstream
from test_system_foundation import activate_user, create_role, create_user


def category_payload(code: str, name: str, parent_id=None):
    return {
        "parentId": parent_id,
        "code": code,
        "name": name,
        "description": name + "说明",
        "sortOrder": 10,
        "enabled": True,
    }


def test_tool_category_tree_versions_and_non_empty_archive(client, administrator):
    parent = result(client.post(
        API + "/categories", headers=administrator,
        json=category_payload("equipment", "设备工具"),
    ), 201)
    child = result(client.post(
        API + "/categories", headers=administrator,
        json=category_payload("equipment.read", "设备读取", parent["id"]),
    ), 201)
    assert child["path"] == "设备工具 / 设备读取"

    cycle = category_payload(parent["code"], parent["name"], child["id"])
    cycle["version"] = parent["version"]
    response = client.patch(API + "/categories/" + parent["id"], headers=administrator, json=cycle)
    assert response.status_code == 422 and "循环" in response.text
    assert client.delete(
        API + f"/categories/{parent['id']}?version={parent['version']}", headers=administrator,
    ).status_code == 409

    update = category_payload("equipment.read", "设备只读", parent["id"])
    update["version"] = child["version"]
    changed = result(client.patch(
        API + "/categories/" + child["id"], headers=administrator, json=update,
    ))
    assert changed["version"] == child["version"] + 1
    assert client.patch(
        API + "/categories/" + child["id"], headers=administrator, json=update,
    ).status_code == 409

    assert result(client.delete(
        API + f"/categories/{child['id']}?version={changed['version']}", headers=administrator,
    ))["deleted"] is True
    assert result(client.delete(
        API + f"/categories/{parent['id']}?version={parent['version']}", headers=administrator,
    ))["deleted"] is True
    exported = client.get(API + "/categories-export", headers=administrator)
    assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/csv")


def test_method_metadata_disables_real_execution_and_batch_is_atomic(client, administrator):
    category = result(client.post(
        API + "/categories", headers=administrator,
        json=category_payload("platform", "平台方法"),
    ), 201)
    methods = result(client.get(API + "/methods", headers=administrator))["items"]
    current = next(item for item in methods if item["name"] == "current_datetime")
    changed = result(client.patch(
        API + "/methods/current_datetime", headers=administrator,
        json={
            "version": current["methodVersion"],
            "categoryId": category["id"],
            "code": "platform.current_datetime",
            "displayName": "读取当前时间",
            "description": "返回平台当前日期与时间",
            "enabled": False,
            "tags": ["时间", "基础"],
        },
    ))
    assert changed["enabled"] is False and changed["categoryPath"] == "平台方法"
    tools = result(client.get(API + "/tools", headers=administrator))["items"]
    assert all(item["name"] != "current_datetime" for item in tools)
    assert client.post(
        API + "/tools/current_datetime/validate", headers=administrator, json={"arguments": {}},
    ).status_code == 404
    assert client.delete(
        API + f"/categories/{category['id']}?version={category['version']}", headers=administrator,
    ).status_code == 409

    batch = result(client.post(
        API + "/methods/batch", headers=administrator,
        json={"items": [{
            "toolName": "current_datetime",
            "version": changed["methodVersion"],
            "categoryId": None,
            "code": changed["code"],
            "displayName": changed["displayName"],
            "description": changed["description"],
            "enabled": True,
            "tags": changed["tags"],
        }]},
    ))
    assert batch["count"] == 1 and batch["items"][0]["enabled"] is True
    assert result(client.post(
        API + "/tools/current_datetime/validate", headers=administrator, json={"arguments": {}},
    ))["valid"] is True
    assert result(client.delete(
        API + f"/categories/{category['id']}?version={category['version']}", headers=administrator,
    ))["deleted"] is True
    exported = client.get(API + "/methods-export", headers=administrator)
    assert exported.status_code == 200 and "current_datetime" in exported.text


def test_method_test_reuses_persisted_private_network_authorization(client, administrator, upstream):
    payload = http_payload(name="本机计算接口", tool_name="test_get_calculate")
    payload["config"].update({
        "baseUrl": upstream[0],
        "path": "/records",
        "method": "GET",
        "bodyMode": "query",
        "allowPrivate": True,
    })
    payload["tools"][0]["permissions"] = []
    created = create_http(client, administrator, payload)
    published = result(client.post(
        API + f"/connectors/{created['id']}/publish",
        headers=administrator,
        json={"version": 1},
    ))
    assert published["versions"][0]["config"]["allow_private"] is True

    method = next(item for item in result(
        client.get(API + "/methods?keyword=test_get_calculate", headers=administrator),
    )["items"] if item["name"] == "test_get_calculate")
    assert method["connectorId"] == created["id"]
    assert method["connectorName"] == "本机计算接口"

    tested = result(client.post(
        API + "/methods/test_get_calculate/test",
        headers=administrator,
        json={"arguments": {"deviceCode": "LAN-001"}},
    ))
    assert tested["status"] == "success"
    assert tested["provider"] == "http"
    assert tested["connectorId"] == created["id"]
    assert tested["data"]["items"][0]["device"] == "A"
    assert upstream[1][-1]["path"] == "/records?deviceCode=LAN-001"


def test_connector_clone_share_and_archive_retain_source_history(client, administrator, db):
    source = create_http(
        client, administrator,
        http_payload(name="设备能力源", tool_name="factory.source_status"),
    )
    result(client.post(
        API + f"/connectors/{source['id']}/publish",
        headers=administrator, json={"version": 1},
    ))
    copied = result(client.post(
        API + f"/connectors/{source['id']}/clone",
        headers=administrator,
        json={"name": "设备能力副本", "toolPrefix": "copied"},
    ), 201)
    assert copied["status"] == "draft" and copied["visibility"] == "private"
    assert copied["versions"][0]["tools"][0]["name"] == "copied.factory.source_status"
    assert client.post(
        API + f"/connectors/{copied['id']}/share",
        headers=administrator, json={"version": 99, "visibility": "workspace"},
    ).status_code == 409
    shared = result(client.post(
        API + f"/connectors/{copied['id']}/share",
        headers=administrator, json={"version": 1, "visibility": "workspace"},
    ))
    assert shared["visibility"] == "workspace"
    result(client.delete(
        API + f"/connectors/{copied['id']}?version=1", headers=administrator,
    ))
    db.expire_all()
    assert db.get(Connector, copied["id"]).status == "archived"
    assert db.get(Connector, source["id"]).status == "published"


def test_skill_and_plugin_clone_share_archive_keep_versions(client, administrator, db):
    skill = client.post("/api/skills", headers=administrator, json={
        "name": "设备复核",
        "description": "复核设备异常",
        "spec": {"instructions": "核对设备、指标和原始数据。", "rules": ["保留证据"]},
    })
    assert skill.status_code == 201, skill.text
    skill_id = skill.json()["skill_id"]
    copied_skill = client.post(
        f"/api/skills/{skill_id}/clone", headers=administrator,
        json={"new_name": "设备复核副本"},
    )
    assert copied_skill.status_code == 201, copied_skill.text
    copied_skill_id = copied_skill.json()["skill_id"]
    assert client.post(
        f"/api/skills/{copied_skill_id}/share", headers=administrator,
        json={"visibility": "private"},
    ).status_code == 200
    assert client.post(
        f"/api/skills/{copied_skill_id}/archive", headers=administrator, json={},
    ).status_code == 200

    plugin = client.post("/api/plugins", headers=administrator, json={
        "name": "设备能力包",
        "description": "复用设备复核方法",
        "spec": {"instructions": "先复核再输出。", "skill_refs": [skill_id], "tool_refs": []},
    })
    assert plugin.status_code == 201, plugin.text
    plugin_id = plugin.json()["plugin_id"]
    copied_plugin = client.post(
        f"/api/plugins/{plugin_id}/clone", headers=administrator,
        json={"new_name": "设备能力包副本"},
    )
    assert copied_plugin.status_code == 201, copied_plugin.text
    copied_plugin_id = copied_plugin.json()["plugin_id"]
    assert client.post(
        f"/api/plugins/{copied_plugin_id}/share", headers=administrator,
        json={"visibility": "workspace"},
    ).status_code == 200
    assert client.post(
        f"/api/plugins/{copied_plugin_id}/archive", headers=administrator, json={},
    ).status_code == 200

    db.expire_all()
    assert db.get(Skill, copied_skill_id).status == "archived"
    assert db.get(Plugin, copied_plugin_id).status == "archived"
    assert all(item["id"] != copied_skill_id for item in client.get("/api/skills", headers=administrator).json()["items"])
    assert all(item["id"] != copied_plugin_id for item in client.get("/api/plugins", headers=administrator).json()["items"])
    assert db.scalar(select(Skill).where(Skill.id == skill_id)) is not None
    assert db.scalar(select(Plugin).where(Plugin.id == plugin_id)) is not None


def test_capability_catalog_mutation_respects_role_permissions(client, administrator):
    role = create_role(client, administrator, code="catalog_reader", permissions=[
        "home.view", "capabilities.view",
    ], scope="workspace")
    create_user(client, administrator, [role["id"]], username="catalog-reader")
    reader = activate_user(client, username="catalog-reader")
    assert client.get(API + "/categories", headers=reader).status_code == 200
    assert client.get(API + "/methods", headers=reader).status_code == 200
    assert client.post(
        API + "/categories", headers=reader,
        json=category_payload("forbidden", "不可创建"),
    ).status_code == 403
    assert client.patch(
        API + "/methods/current_datetime", headers=reader,
        json={"version": 0, "code": "current_datetime", "enabled": False},
    ).status_code == 403
    assert client.get(API + "/methods-export", headers=reader).status_code == 403
