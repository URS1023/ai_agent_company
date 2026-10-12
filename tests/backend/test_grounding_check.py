"""交付落地校验器测试（隔离，无 DB/网络业务依赖）。"""
from datetime import datetime

from sqlalchemy import select

from app.ids import new_id
from app.models import Run, ToolCall, User
from app.modules.runtime.grounding_check import (
    artifacts_for_run, check_grounding, evidence_from_run, extract_numbers,
)


def test_extract_numbers_picks_numbers_with_units_deduplicated():
    text = "近30天应检未检7台设备，累计402次任务；逾期60天以上的有4855条，占比86.7%。7台设备重复。"
    claims = extract_numbers(text)
    values = [(claim["value"], claim["unit"]) for claim in claims]
    assert ("7", "台") in values
    assert ("402", "次") in values
    assert ("4855", "条") in values
    assert ("86.7", "%") in values
    # 去重：同一 (数字,单位) 只出现一次
    assert values.count(("7", "台")) == 1


def test_check_grounding_pass_when_numbers_traceable():
    report = check_grounding(
        committed_text="共有7台设备应检未检，任务402次，占比86.7%。",
        evidence=["7", "402", "86.7", "近30天应检未检 7 台设备 402 次任务 86.7%"],
        artifacts=[{"id": "a1", "name": "分析.docx"}],
    )
    assert report["status"] == "pass"
    assert report["matched"] >= 3


def test_check_grounding_warns_on_untraceable_numbers():
    report = check_grounding(
        committed_text="共有120台设备应检未检，成本缺口为8500万元。",
        evidence=["7", "402"],
        artifacts=[],
    )
    assert report["status"] == "warn"
    assert report["unmatched"]
    assert any("溯源" in warning for warning in report["warnings"])


def test_check_grounding_warns_when_numbers_without_any_evidence():
    report = check_grounding(committed_text="已确认逾期60天以上4855条。", evidence=[], artifacts=[])
    assert report["status"] == "warn"
    assert any("没有任何成功查询" in warning for warning in report["warnings"])


def _admin_id(db):
    return db.scalar(select(User.id).limit(1))


def test_evidence_from_run_uses_completed_query_receipts(client, administrator, db):
    run = Run(id=new_id(), workspace_id="w-audit", conversation_id="c-audit",
              started_by_user_id=_admin_id(db), status="completed",
              model_profile="reasoning", context_snapshot={}, created_at=datetime.utcnow())
    db.add(run)
    db.add(ToolCall(id=new_id(), workspace_id="w-audit", run_id=run.id,
                    tool_name="query_business_data", status="completed",
                    arguments_json={"query_id": "q-1"},
                    result_json={"status": "success", "data": {
                        "success": True, "results": [{"queryId": "q-1", "rows": [{"台数": 7, "任务": 402}]}]}}))
    db.commit()
    evidence = evidence_from_run(db, run)
    flattened = " ".join(evidence)
    assert "7" in flattened and "402" in flattened
    assert artifacts_for_run(db, run) == []