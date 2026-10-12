"""复合交付与口径选项回复应沿用同一任务，不能丢失文件验收清单。"""
import pytest

from app.business_worker import chat, tasks
from test_business_worker import worker_context, prepare


REQUEST = "查询应收数据做成excel，再做一份ppt用于应收应付，再做一份数字大屏用于展示，再给我一份erp系统介绍的ppt"


def test_compound_request_counts_distinct_presentations():
    assert {row["type"]: row["count"] for row in tasks.requirements(REQUEST)} == {
        "presentation": 2, "spreadsheet": 1, "dashboard": 1}
    assert tasks.requirements("生成两份 PPT") == [{"type": "presentation", "count": 2}]
    assert tasks.requirements("制作一份PPT，PPT需要图表") == [{"type": "presentation", "count": 1}]
    assert {row["type"]: row["count"] for row in tasks.requirements("生成2份Excel和1份PPT")} == {
        "presentation": 1, "spreadsheet": 2}


@pytest.mark.parametrize("user_text,count", [
    ("做两份PPT", 2),
    ("做2份 PPT", 2),
    ("做一份应付PPT和一份ERP PPT", 2),
    ("生成两份应付PPT和一份ERP PPT", 3),
    ("制作一份PPT并在PPT中补充图表", 1),
    ("参考财务PPT做一份ERP PPT", 1),
])
def test_explicit_same_clause_deliveries_are_counted_without_counting_references(user_text, count):
    """分别给出的份数需要全部交付，正文说明和参考文件不能充当新交付。"""
    assert tasks.requirements(user_text) == [{"type": "presentation", "count": count}]


@pytest.mark.parametrize("user_text", [
    "怎么生成PPT？", "别做PPT", "先不要生成PPT，只解释步骤", "说明一下制作PPT的流程",
    "给我解释一下怎么生成PPT", "生成PPT的步骤是什么？", "How do I create a PowerPoint?",
    "Don't generate slides", "Please explain how to create slides", "I do not want you to generate a PPT",
])
def test_questions_and_refusals_do_not_require_deliveries(user_text):
    assert tasks.requirements(user_text) == []


@pytest.mark.parametrize("user_text", [
    "按模板生成PPT", "把这份材料转成幻灯片", "make a ppt", "generate slides",
    "Can you create a PowerPoint?", "能帮我生成PPT吗？", "Please create a presentation",
])
def test_explicit_requests_keep_deliveries_even_with_question_mark(user_text):
    assert tasks.requirements(user_text) == [{"type": "presentation", "count": 1}]


def test_rejected_format_does_not_hide_another_explicit_delivery():
    assert tasks.requirements("不要生成PPT，导出Excel") == [{"type": "spreadsheet", "count": 1}]


def test_creation_applies_to_plain_format_enumeration():
    assert {row["type"]: row["count"] for row in tasks.requirements("生成Excel，PPT和大屏")} == {
        "spreadsheet": 1, "presentation": 1, "dashboard": 1}


def test_negative_action_is_not_inherited_by_following_formats():
    assert tasks.requirements("先不要生成PPT，只生成Excel") == [{"type": "spreadsheet", "count": 1}]
    assert tasks.requirements("先不要生成PPT，Excel和大屏") == []


def test_request_for_creation_plan_does_not_require_actual_artifact():
    assert tasks.requirements("先给我制作PPT的方案") == []


def test_option_answer_keeps_original_delivery_requirements(worker_context):
    initial = prepare(worker_context, REQUEST)
    question = "请确认口径：\n- **A（建议）**：合同相关数据。\n- **B**：只做应付，应收待补充。\n- **C**：提供应收表。"
    first = chat.settle(initial["run_id"], "completed", question)
    assert first["status"] == "partial"
    assert not first["content"].startswith("大屏交付核验未通过")
    continued = prepare(worker_context, "B")
    assert continued["task"]["task_id"] == initial["task"]["task_id"]
    assert continued["task"]["title"] == REQUEST
    assert continued["task"]["required_deliveries"] == initial["task"]["required_deliveries"]
    final = chat.settle(continued["run_id"], "completed", "全部已完成")
    assert final["status"] == "partial"
    assert next(row for row in final["task"]["missing_deliveries"] if row["type"] == "presentation")["missing"] == 2


def test_unoffered_letter_does_not_reuse_task(worker_context):
    initial = prepare(worker_context, "生成一份 PPT")
    chat.settle(initial["run_id"], "completed", "请确认：\nA：通用介绍\nB：企业介绍")
    other = prepare(worker_context, "C")
    assert other["task"]["task_id"] != initial["task"]["task_id"]


def test_letter_after_completed_task_is_independent(worker_context):
    initial = prepare(worker_context, "解释两个选项")
    chat.settle(initial["run_id"], "completed", "请选择：\nA：第一种\nB：第二种")
    other = prepare(worker_context, "B")
    assert other["task"]["task_id"] != initial["task"]["task_id"]
