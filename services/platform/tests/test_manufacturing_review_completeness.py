"""隔离验证混合不合格与缺证据时的人工复核，不读取任何业务数据源。"""
from copy import deepcopy

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import TaskEvent
from agent_platform.core.tasks import TaskService
from agent_platform.modules.manufacturing import models as m
from agent_platform.modules.manufacturing.service import ManufacturingService


@pytest.fixture
def service(tmp_path):
    """每例只在 pytest 新目录建立平台库，观测由显式人工补录入口写入。"""
    engine = create_engine("sqlite:///" + (tmp_path / "review.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    actor = Actor("reviewer", "workspace", frozenset({"*"}), ("admin",))
    try:
        with Session(engine) as session:
            yield ManufacturingService(session, actor)
            session.rollback()
    finally:
        engine.dispose()


def inspection_run(service, target_type, missing):
    """执行真实判定，分别构造缺样本、跨样本缺指标与同样本缺指标。"""
    model = m.Device if target_type == "device" else m.Product
    target = service.create(model, {"name": "复核对象"})
    standard = service.create(m.InspectionTemplate, {
        "name": "完整性标准", "target_type": target_type,
        "metrics": [{"id": field, "name": field, "unit": "g", "standard_max": "10"}
                    for field in ("weight", "component")],
    })
    task = service.create_inspection({
        "name": "完整性检验", "target_type": target_type,
        "device_id" if target_type == "device" else "product_id": target["id"],
        "template_id": standard["id"], "template_revision": standard["revision"],
        "expected_samples": 2 if missing in {"sample", "sample_metric"} else 1,
    })
    sample = service.add_sample(task["id"], {"external_ref": "S1"})
    observations = [{"metric_id": "weight", "value": "20", "unit": "g"}]
    if missing != "metric":
        observations.append({"metric_id": "component", "value": "5", "unit": "g"})
    evidence = service.add_evidence(task["id"], {
        "revision": sample["inspection_revision"], "sample_id": sample["id"],
        "observations": observations,
    })
    revision = evidence["inspection_revision"]
    if missing == "sample_metric":
        second = service.add_sample(task["id"], {"external_ref": "S2"})
        evidence = service.add_evidence(task["id"], {
            "revision": second["inspection_revision"], "sample_id": second["id"],
            "observations": [{"metric_id": "weight", "value": "5", "unit": "g"}],
        })
        revision = evidence["inspection_revision"]
    return service.run_inspection(task["id"], revision)


@pytest.mark.parametrize("target_type", ["device", "product"])
@pytest.mark.parametrize("missing", ["sample", "sample_metric", "metric"])
def test_mixed_failed_and_missing_cannot_review_passed(service, target_type, missing):
    inspection = inspection_run(service, target_type, missing)
    result = deepcopy(inspection["result"])
    assert inspection["outcome"] == "failed"
    if missing == "sample":
        assert result["missing_samples"] == 1
    elif missing == "sample_metric":
        assert [sample["outcome"] for sample in result["samples"]] == ["failed", "incomplete"]
    else:
        assert result["samples"][0]["outcome"] == "failed"
        assert [metric["outcome"] for metric in result["samples"][0]["metrics"]] == ["failed", "incomplete"]
    task_id = result["execution_task_id"]
    tasks = TaskService(service.session, service.actor)
    before_task = tasks.get(task_id)
    before_events = service.session.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == task_id))
    with pytest.raises(DomainError) as error:
        service.review(inspection["id"], {
            "revision": inspection["revision"], "outcome": "passed", "note": "申请合格复核",
        })
    assert error.value.code == "missing_evidence"
    # 拒绝发生在任何写入之前，原运行、任务版本和复核事件均不改变。
    service.session.expire_all()
    current = service.get(m.Inspection, inspection["id"])
    assert (current.revision, current.status, current.outcome) == (inspection["revision"], "awaiting_review", "failed")
    assert current.result == result
    assert service.session.get(m.InspectionRun, result["run_id"]).result == result
    assert tasks.get(task_id) == before_task
    assert service.session.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == task_id)) == before_events
    assert service.session.scalar(select(func.count()).select_from(m.InspectionReview)) == 0
    # 缺证据仍可按不合格处置，不应让任务卡在无法记录复核的状态。
    reviewed = service.review(inspection["id"], {
        "revision": inspection["revision"], "outcome": "failed", "note": "不合格并隔离缺失证据的样本",
    })
    assert reviewed["inspection"]["status"] == "reviewed"
    assert tasks.get(task_id)["status"] == "succeeded"


@pytest.mark.parametrize("target_type", ["device", "product"])
def test_complete_failed_evidence_allows_reasoned_manual_override(service, target_type):
    inspection = inspection_run(service, target_type, None)
    result = deepcopy(inspection["result"])
    assert inspection["outcome"] == "failed"
    assert result["missing_samples"] == 0
    assert all(metric["outcome"] != "incomplete" for sample in result["samples"] for metric in sample["metrics"])
    reviewed = service.review(inspection["id"], {
        "revision": inspection["revision"], "outcome": "passed", "note": "证据齐全，现场按偏差审批记录人工改判",
    })
    service.session.expire_all()
    current = service.get(m.Inspection, inspection["id"])
    assert (current.status, current.outcome) == ("reviewed", "passed")
    assert current.result == result
    assert reviewed["review"]["note"] == "证据齐全，现场按偏差审批记录人工改判"
    assert service.session.get(m.InspectionRun, result["run_id"]).result == result
    assert TaskService(service.session, service.actor).get(result["execution_task_id"])["status"] == "succeeded"
