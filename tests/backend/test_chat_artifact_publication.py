"""公共工件卡片只公开当前身份可读取的文件，旧引擎导入继续兼容。"""
from __future__ import annotations

import pytest

from app.errors import PermissionErrorEx
from app.modules.artifact.service import create_artifact
from app.modules.chat_artifacts.artifacts import persist_artifacts
from app.modules.runtime.service import create_run
from app.modules.runtime.service import create_message
from app.modules.harness.runtime import _migration_background
from app.providers.tool.base import ToolResult
from test_operations import user_id
from test_system_foundation import activate_user, create_role, create_user


def test_public_artifact_module_keeps_legacy_import_identity():
    from app.modules.chat_artifacts import artifacts as shared
    from app.modules.openwebui import artifacts as legacy
    assert legacy is shared


@pytest.mark.parametrize("through_data", [False, True])
def test_artifact_reference_checks_same_workspace_user_scope(client, administrator, db, through_data):
    workspace_id = administrator["X-Workspace-Id"]
    admin_id = user_id(client, administrator)
    role = create_role(client, administrator, code="artifact_reader", permissions=["home.view", "files.view", "chat.view"], scope="self")
    create_user(client, administrator, [role["id"]], username="artifact-reader")
    reader = activate_user(client, username="artifact-reader")
    reader_id = user_id(client, reader)
    run = create_run(db, workspace_id=workspace_id, conversation_id="", user_id=reader_id)
    own = create_artifact(db, workspace_id=workspace_id, run_id=run.id, owner_user_id=reader_id,
                          type="document", name="本人文件.txt", data=b"own synthetic")
    foreign = create_artifact(db, workspace_id=workspace_id, run_id=run.id, owner_user_id=admin_id,
                              type="document", name="他人保密文件.txt", data=b"foreign synthetic")
    db.commit()

    def result(artifact_id):
        return ToolResult(status="success", data={"artifact_id": artifact_id} if through_data else {},
                          artifacts=[] if through_data else [{"id": artifact_id}])

    authorized = result(own.id)
    assert persist_artifacts(db, run, "create_document", authorized, {})[0]["id"] == own.id
    denied = result(foreign.id)
    with pytest.raises((ValueError, PermissionErrorEx)):
        persist_artifacts(db, run, "create_document", denied, {})
    assert "他人保密文件" not in str(denied.to_dict())

    history = create_message(db, workspace_id=workspace_id, conversation_id=run.conversation_id,
        role="assistant", content="旧回复正文", parts=[
            {"type": "artifact", "artifact": {"id": own.id, "name": "不信任旧名称"}},
            {"type": "artifact", "artifact": {"id": foreign.id, "name": "不能泄露的他人名称"}},
            {"type": "tool_result", "result": {"secret": "不迁移内部工具日志"}},
        ])
    db.commit()
    background = _migration_background(db, run, "current-user", "current-assistant")
    assert own.id in background and "本人文件.txt" in background
    assert foreign.id not in background and "他人名称" not in background and "他人保密" not in background
    assert "不信任旧名称" not in background and "内部工具日志" not in background
