"""四维业务复核评分器测试（纯离线夹具）。"""
import copy
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import business_review_scorer as scorer


def _query_result(value):
    return {"success": True, "rows": [{"数值": value}]}


GOOD = {
    "question": "近一个月有多少设备应检未检？",
    "answer": "应检未检 402 次任务，涉及 7 台设备；数据来自本轮查询回执。",
    "tool_calls": [
        {"name": "query_business_data", "status": "completed", "datasource_id": "ds-local",
         "result": {"status": "success", "data": {"results": [{"rows": [{"任务": 402}, {"设备": 7}]}]}}},
    ],
    "artifacts": [{"id": "a1", "name": "分析.docx", "type": "document"}],
    "allowed_datasource_ids": ["ds-local"],
    "status": "completed",
}


def test_good_example_passes_all_dims():
    result = scorer.score_example(GOOD)
    assert result["verdict"] == 1
    assert set(result["dims"].values()) == {1}


def test_fabricated_number_fails_d1():
    bad = copy.deepcopy(GOOD)
    bad["answer"] = "近30天应检未检 402 次任务，涉及 120 台设备。"
    result = scorer.score_example(bad)
    assert result["dims"]["D1"] == 0 and result["verdict"] == 0


def test_failed_tool_written_as_success_fails_d2():
    bad = copy.deepcopy(GOOD)
    bad["answer"] = "查询完成，一切正常。"
    bad["tool_calls"][0] = {"name": "query_business_data", "status": "failed",
                            "error_code": "database_timeout", "result": None}
    result = scorer.score_example(bad)
    assert result["dims"]["D2"] == 0


def test_out_of_scope_datasource_fails_d3():
    bad = copy.deepcopy(GOOD)
    bad["tool_calls"][0]["datasource_id"] = "ds-other"
    result = scorer.score_example(bad)
    assert result["dims"]["D3"] == 0


def test_claims_generated_without_artifact_fails_d4():
    bad = copy.deepcopy(GOOD)
    bad["answer"] = "分析完成，已生成 Word 报告交付。"
    bad["artifacts"] = []
    result = scorer.score_example(bad)
    assert result["dims"]["D4"] == 0


def test_running_tool_fails_d4():
    bad = copy.deepcopy(GOOD)
    bad["tool_calls"] = bad["tool_calls"] + [{"name": "render_document", "status": "running"}]
    result = scorer.score_example(bad)
    assert result["dims"]["D4"] == 0


def test_score_matrix_aggregates():
    matrix = scorer.score_matrix([copy.deepcopy(GOOD), copy.deepcopy(dict(GOOD, answer="合计 9999 件，无回执来源。"))])
    assert matrix["summary"]["total"] == 2
    assert matrix["summary"]["passed"] == 1
    assert matrix["summary"]["rate"] == "1/2"