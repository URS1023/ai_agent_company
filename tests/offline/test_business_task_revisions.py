"""跨轮交付清单回归：使用内存伪对象和伪存储，不连接数据库或模型。"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.business_worker import tasks
from app.models import Artifact


KINDS = ("json", "spreadsheet", "document", "presentation")


class ScalarRows(list):
    """提供 SQLAlchemy 标量结果所需的最小离线接口。"""

    def all(self):
        return list(self)


class MemoryRows:
    """仅保存当前夹具的运行和产物，不创建数据库引擎。"""

    def __init__(self, runs, artifacts):
        self.runs = runs
        self.artifacts = artifacts

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        return ScalarRows(self.artifacts if entity is Artifact else reversed(self.runs))


def artifact(kind, run_id):
    """伪存储中的工件，验证仍通过真实 deliveries 聚合函数。"""
    identity = run_id + "-" + kind
    return SimpleNamespace(id=identity, type=kind, name=identity, run_id=run_id,
                           storage_key=identity, status="ready", size=1, mime_type="application/octet-stream")


@pytest.fixture
def completed_task(monkeypatch):
    """首轮已交付四文件，后续轮次共用同一任务和稳定草稿。"""
    original = {
        "id": "task-1", "task_id": "task-1", "draft_id": "draft-1", "status": "completed",
        "required_deliveries": [{"type": kind, "count": 1} for kind in KINDS], "missing_deliveries": [],
    }
    first = SimpleNamespace(id="run-1", workspace_id="workspace-1", started_by_user_id="user-1",
                            conversation_id="conversation-1", context_snapshot={"task_id": "task-1", "business_task": original})
    db = MemoryRows([first], [artifact(kind, first.id) for kind in KINDS])
    monkeypatch.setattr("app.common.storage.get_storage", lambda: SimpleNamespace(exists=lambda key: bool(key)))
    return db, first


def next_run(db, content, *, required_deliveries=None, task_id="task-1"):
    """调用真实任务选择函数，将新轮次加入伪持久化集合。"""
    payload = SimpleNamespace(content=content, task_id=task_id, required_deliveries=required_deliveries,
                              datasource_ids=[], file_ids=[], knowledge_base_ids=[], template_id=None, dashboard_template_id=None)
    chosen = tasks.choose_task(db, SimpleNamespace(workspace_id="workspace-1", user_id="user-1"), "conversation-1", payload)
    run = SimpleNamespace(id="run-2", workspace_id="workspace-1", started_by_user_id="user-1",
                          conversation_id="conversation-1", context_snapshot={"task_id": chosen["task_id"], "business_task": chosen})
    db.runs.append(run)
    return run


@pytest.mark.parametrize("explicit", [True, False])
def test_four_deliveries_then_only_new_presentation_keeps_task_and_history(completed_task, explicit):
    db, first = completed_task
    original = deepcopy(first.context_snapshot)
    expected = [{"type": "presentation", "count": 1}]
    revised = next_run(db, "只把刚才 PPT 第3页标题修改为周期核对，生成新版 PPT。其他已交付文件保留即可。",
                       required_deliveries=expected if explicit else None)
    chosen = revised.context_snapshot["business_task"]
    assert chosen["task_id"] == "task-1" and chosen["draft_id"] == "draft-1"
    assert chosen["required_deliveries"] == expected and chosen["requires_new_delivery"] is True
    db.artifacts.append(artifact("presentation", revised.id))
    status, task, delivered = tasks.verify(db, revised, "completed")
    assert status == "completed" and task["missing_deliveries"] == []
    assert {item["id"] for item in delivered} == {item.id for item in db.artifacts}
    assert len(delivered) == 5
    assert first.context_snapshot == original


def test_revision_without_new_presentation_cannot_use_previous_file(completed_task):
    db, _first = completed_task
    revised = next_run(db, "修改刚才的 PPT 并生成新版", required_deliveries=[{"type": "presentation", "count": 1}])
    status, task, delivered = tasks.verify(db, revised, "completed")
    assert status == "partial"
    assert task["missing_deliveries"] == [{"type": "presentation", "count": 1, "missing": 1}]
    assert len(delivered) == 4


def test_plain_continue_keeps_original_requirements_and_previous_valid_deliveries(completed_task):
    db, first = completed_task
    first.context_snapshot["business_task"].update(
        status="partial", missing_deliveries=[{"type": "presentation", "count": 1, "missing": 1}])
    db.artifacts = [item for item in db.artifacts if item.type != "presentation"]
    continued = next_run(db, "继续", task_id=None)
    chosen = continued.context_snapshot["business_task"]
    assert chosen["task_id"] == "task-1" and chosen["draft_id"] == "draft-1"
    assert chosen["required_deliveries"] == [{"type": kind, "count": 1} for kind in KINDS]
    assert chosen["missing_deliveries"] == [] and chosen["requires_new_delivery"] is False
    status, task, _delivered = tasks.verify(db, continued, "completed")
    assert status == "partial" and task["missing_deliveries"] == [{"type": "presentation", "count": 1, "missing": 1}]
    db.artifacts.append(artifact("presentation", continued.id))
    status, task, delivered = tasks.verify(db, continued, "completed")
    assert status == "completed" and len(delivered) == 4


def test_explicit_empty_delivery_contract_is_respected(completed_task):
    db, _first = completed_task
    discussed = next_run(db, "只讨论修改方案，先不生成附件", required_deliveries=[])
    assert discussed.context_snapshot["business_task"]["required_deliveries"] == []
    status, task, delivered = tasks.verify(db, discussed, "completed")
    assert status == "completed" and task["missing_deliveries"] == [] and len(delivered) == 4
