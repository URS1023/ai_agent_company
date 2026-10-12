"""业务技能清单只读接口测试（P2.5；隔离 SQLite）。"""
from test_system_foundation import activate_user, create_role, create_user


def test_business_inventory_endpoint_lists_skills_and_drift(client, administrator):
    response = client.get("/api/skills/business-inventory", headers=administrator)
    assert response.status_code == 200, response.text
    body = response.json()
    names = {item["skill_name"] for item in body["skills"]}
    # 至少包含本轮新增的三个技能包
    assert {"data-analysis", "dashboard-creation", "presentation"} <= names
    for item in body["skills"]:
        assert "resources" not in item  # 只读接口不回传正文/资源清单
        assert item["status"] in {"valid", "invalid"}
    assert "manifest" in body and "drift" in body
    assert isinstance(body["drift"]["drifted"], list)


def test_business_inventory_requires_skills_view_permission(client, administrator):
    """缺少 skills.view 的普通成员不能读取业务技能清单。"""
    role = create_role(client, administrator, code="no_skills", permissions=["home.view"])
    create_user(client, administrator, [role["id"]], username="no-skills-user")
    member = activate_user(client, username="no-skills-user")
    response = client.get("/api/skills/business-inventory", headers=member)
    assert response.status_code in {401, 403}
