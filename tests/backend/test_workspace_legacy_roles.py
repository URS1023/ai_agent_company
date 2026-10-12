"""Legacy membership writes preserve the same governance as the current UI."""
from sqlalchemy import select

from app.models import WorkspaceMember
from app.modules.system.models import Role, UserRole
from test_system_foundation import create_user


def test_legacy_member_endpoint_cannot_demote_last_admin(client, administrator, db):
    user_id = client.get("/api/auth/me", headers=administrator).json()["user"]["id"]
    response = client.post("/api/workspace/members", headers=administrator,
                           json={"user_id": user_id, "role": "member"})
    assert response.status_code == 409, response.text
    db.expire_all()
    assert db.scalar(select(WorkspaceMember).where(WorkspaceMember.user_id == user_id)).role == "admin"
    assert client.get("/api/v1/system/users", headers=administrator).status_code == 200


def test_legacy_member_role_updates_explicit_role_assignments(client, administrator, db):
    workspace_id = administrator["X-Workspace-Id"]
    role = db.scalar(select(Role).where(Role.workspace_id == workspace_id, Role.code == "member"))
    user = create_user(client, administrator, [role.id], username="legacy-role-member")
    for requested, expected in [("admin", "workspace_admin"), ("member", "member")]:
        response = client.post("/api/workspace/members", headers=administrator,
                               json={"user_id": user["id"], "role": requested})
        assert response.status_code == 201, response.text
        db.expire_all()
        assigned = db.scalars(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(
            UserRole.workspace_id == workspace_id, UserRole.user_id == user["id"],
        )).all()
        assert assigned == [expected]
