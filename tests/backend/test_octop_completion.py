"""使用合成原生输出验证 Octop 闭环规则，不访问模型、业务库或外部网络。"""
from __future__ import annotations

import json

import pytest

from app.modules.octop_bridge.completion import inspect_completion


def call(call_id="call-1", name="query_business_data", arguments='{"limit": 1}', **extra):
    """生成已收齐参数的合成调用。"""
    return {"type": "function_call", "call_id": call_id, "name": name,
            "arguments": arguments, "status": "completed", **extra}


def result(call_id="call-1", *, payload=None, **extra):
    """生成网关回执在 Open WebUI 中的真实包装结构。"""
    return {"type": "function_call_output", "call_id": call_id, "status": "completed",
            "output": [{"type": "input_text", "text": json.dumps(
                payload if payload is not None else {"status": "success", "data": {"count": 1}})}], **extra}


def answer(text="本次合成查询返回 1 条设备记录。", **extra):
    """生成工具之后的最终助手答复。"""
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text}], **extra}


def codes(summary):
    return {item["code"] for item in summary["errors"]}


def test_plain_answer_has_no_tool_or_progress_requirement():
    summary = inspect_completion([answer("我会使用中文回答。")])
    assert summary["complete"]
    assert summary["tool_calls"] == 0


def test_nonstream_plain_answer_accepts_saved_content():
    assert inspect_completion(None, fallback_text="你好。")['complete']


@pytest.mark.parametrize("text", ["让我继续分析。", "我来继续处理。", "接下来生成报告。"])
def test_progress_without_any_tool_still_requires_a_real_answer(text):
    summary = inspect_completion([], fallback_text=text)
    assert not summary["complete"]
    assert "progress_only_answer" in codes(summary)


def test_concrete_analysis_conclusion_without_tools_is_not_progress():
    assert inspect_completion([], fallback_text="分析结果是：需先确认统计周期。")['complete']


def test_successful_tool_calls_expose_only_identity_and_order():
    summary = inspect_completion([call(arguments={"query": "private business SQL"}), result(), answer()])
    assert summary["successful_tool_calls"] == [{"call_id": "call-1", "name": "query_business_data", "index": 1}]
    assert "private business SQL" not in repr(summary)


def test_complete_tool_sequence_extracts_only_final_answer():
    summary = inspect_completion([answer("让我先查询设备。"), call(), result(), answer()])
    assert summary["complete"]
    assert summary["final_answer"] == "本次合成查询返回 1 条设备记录。"
    assert summary["errors"] == []


def test_pre_tool_progress_or_folded_content_cannot_replace_final_answer():
    summary = inspect_completion([answer("现在让我查询设备。"), call(), result()],
                                 fallback_text="已经生成完毕。")
    assert not summary["complete"]
    assert "missing_final_answer" in codes(summary)


@pytest.mark.parametrize("text", [
    "继续完成剩余的 PPT 页面和生成报告", "让我继续完善 PPT 内容并生成图表。",
    "现在让我查询数据库获取更详细的设备巡检统计信息",
    "继续写入 PPT 内容并创建数字大屏\n继续完成剩余的 PPT 页面和生成报告",
    "Let me continue generating the presentation.",
])
def test_progress_only_after_tools_does_not_mean_finished(text):
    summary = inspect_completion([call(), result(), answer(text)])
    assert not summary["complete"]
    assert "progress_only_answer" in codes(summary)


def test_actual_answer_with_a_followup_sentence_is_allowed():
    summary = inspect_completion([call(), result(), answer(
        "本次查询返回 1 条设备记录。\n我会继续按照该口径回答后续问题。")])
    assert summary["complete"]


def test_call_without_matching_result_is_incomplete():
    summary = inspect_completion([call(), answer()])
    assert not summary["complete"]
    assert summary["missing_tool_results"] == ["call-1"]


@pytest.mark.parametrize("arguments", ['{"limit":', "", None, "[1,2]", "null", '{"value": NaN}',
                                        {"value": float("nan")}, {"value": object()}])
def test_arguments_must_be_complete_json_object(arguments):
    summary = inspect_completion([call(arguments=arguments), result(), answer()])
    assert not summary["complete"]
    assert "incomplete_tool_arguments" in codes(summary)


def test_already_parsed_arguments_are_accepted():
    assert inspect_completion([call(arguments={}), result(), answer()])["complete"]


@pytest.mark.parametrize("position", [0, 1, 2])
def test_in_progress_call_result_or_answer_blocks_completion(position):
    output = [call(), result(), answer()]
    output[position]["status"] = "in_progress"
    summary = inspect_completion(output)
    assert not summary["complete"]
    assert summary["unfinished_ids"]


@pytest.mark.parametrize("error", [{"content": "Client session disconnected."}, "upstream_error"])
def test_native_error_cannot_be_overridden_by_a_final_answer(error):
    summary = inspect_completion([call(), result(), answer()], error=error)
    assert not summary["complete"]
    assert "native_error" in codes(summary)
    assert "Client session disconnected" not in repr(summary["errors"])


def test_not_done_and_explicit_stream_error_are_not_success():
    summary = inspect_completion([answer(), {"type": "error", "error": "lost"}], done=False)
    assert not summary["complete"]
    assert {"native_not_done", "output_error"}.issubset(codes(summary))


def test_failed_tool_result_is_closed_but_not_successful():
    summary = inspect_completion([call(), result(payload={"status": "failed", "error_code": "invalid_sql"}), answer()])
    assert summary["execution_complete"]
    assert not summary["complete"]
    assert summary["failed_tool_calls"] == [{"call_id": "call-1", "name": "query_business_data"}]


def test_native_failed_status_can_recover_after_same_tool_succeeds():
    summary = inspect_completion([
        call(status="failed"), result(status="failed"),
        call("call-2", arguments={"limit": 2}), result("call-2"), answer(),
    ])
    assert summary["complete"]
    assert summary["failed_tool_calls"] == []
    assert summary["recovered_tool_calls"] == [{"call_id": "call-1", "name": "query_business_data"}]


def test_failure_after_an_earlier_success_is_not_recovered():
    summary = inspect_completion([
        call(), result(), call("call-2"), result("call-2", status="failed"), answer(),
    ])
    assert not summary["complete"]
    assert summary["failed_tool_calls"][0]["call_id"] == "call-2"


def test_unrelated_tool_success_does_not_hide_failure():
    summary = inspect_completion([
        call(), result(status="failed"), call("call-2", name="render_document"), result("call-2"), answer(),
    ])
    assert not summary["complete"]
    assert summary["recovered_tool_calls"] == []


@pytest.mark.parametrize("output, expected", [
    ([result(), answer()], "orphan_tool_result"),
    ([result(), call(), answer()], "result_before_call"),
    ([call(), call(), result(), answer()], "duplicate_call"),
    ([call(), result(), result(), answer()], "duplicate_result"),
    ([call(arguments={}), {"type": "function_call_output", "call_id": "call-1"}, answer()], "missing_result_payload"),
    ([call(name=""), result(), answer()], "missing_tool_name"),
    ([call(name=["bad"]), result(), answer()], "missing_tool_name"),
    ([call(status={}), result(), answer()], "invalid_output_status"),
    ([call(), result(status=[]), answer()], "invalid_output_status"),
    ([call(call_id="", id=""), answer()], "missing_call_id"),
    ("malformed output", "invalid_output"),
    (["malformed item", answer()], "invalid_output_item"),
])
def test_corrupt_record_does_not_pass(output, expected):
    summary = inspect_completion(output)
    assert not summary["complete"]
    assert expected in codes(summary)


def test_interleaved_parallel_calls_close_by_identity_not_position():
    summary = inspect_completion([call("a"), call("b"), result("b"), result("a"), answer()])
    assert summary["complete"]


def test_business_text_containing_error_word_is_not_a_failed_tool():
    summary = inspect_completion([call(), result(payload={"status": "success", "data": {"error": "故障设备"}}), answer()])
    assert summary["complete"]


def test_reasoning_without_visible_answer_is_incomplete():
    summary = inspect_completion([{"type": "reasoning", "status": "completed", "content": "推理"}])
    assert not summary["complete"]
    assert "missing_final_answer" in codes(summary)


@pytest.mark.parametrize("text", [
    "我来分析：应检20台，未检3台。",
    "我来分析巡检结果，已经完成全部统计。",
    "继续查看生成的报告：[设备报告](/api/artifacts/example/download)",
    "现在让我说明分析结论是本期无需巡检。",
])
def test_progress_heuristic_does_not_reject_results_or_artifact_links(text):
    """最终结论可能保留口语前缀，数值和完成陈述交给业务核验负责。"""
    summary = inspect_completion([call(), result(), answer(text)])
    assert summary["complete"]
    assert "progress_only_answer" not in codes(summary)
