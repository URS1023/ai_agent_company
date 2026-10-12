"""失败台账的跨消息、恢复与有限产物替代回归，完全不启动应用和数据库。"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


_PATH = Path(__file__).resolve().parents[2] / "backend/app/modules/openwebui/tool_failures.py"
_SPEC = spec_from_file_location("isolated_tool_failures", _PATH)
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
ToolFailureLedger = _MODULE.ToolFailureLedger


def failure(name="query_business_data", identity="failed"):
    return {"call_id": identity, "name": name}


def success(name="query_business_data", identity="success", index=1):
    return {"call_id": identity, "name": name, "index": index}


def completed_delivery(required=None, files=None, **changes):
    return {"required": required or {"docx": True}, "missing": [],
            "files": files if files is not None else [{"id": "word", "kind": "docx"}], **changes}


def observed_failure(name, args=None):
    ledger = ToolFailureLedger()
    ledger.observe_tool("first-message", "failed", name, args or {}, {"status": "failed"})
    return ledger


@pytest.mark.parametrize("name", ["query_business_data", "python_execute", "execute_python", "run_workflow",
    "update_business_dashboard", "write_file", "delete_file", "unknown_tool"])
def test_any_word_file_cannot_hide_business_or_unknown_tool_failure(name):
    ledger = observed_failure(name)
    assert ledger.effective(completed_delivery())[0]["name"] == name


def test_failure_survives_native_message_switch_and_plain_successful_reply():
    ledger = ToolFailureLedger()
    ledger.observe_completion("first-message", {"failed_tool_calls": [failure()]})
    ledger.observe_completion("second-message", {"complete": True, "final_answer": "任务已完成。"})
    assert ledger.effective(completed_delivery())[0]["name"] == "query_business_data"


def test_different_tool_success_cannot_clear_query_failure():
    ledger = observed_failure("query_business_data")
    ledger.observe_completion("second-message", {"successful_tool_calls": [success("render_document")]})
    assert ledger.effective(completed_delivery())


def test_later_same_tool_success_records_limited_resolution():
    ledger = observed_failure("query_business_data")
    ledger.observe_completion("second-message", {"successful_tool_calls": [success()]})
    assert ledger.effective() == []
    status = ledger.status()
    assert status["recovered"][0]["resolution"] == "same_tool_later_success"
    assert "不证明原任务业务语义等价" in status["validation_scope"]


def test_earlier_success_does_not_clear_later_failure():
    ledger = ToolFailureLedger()
    ledger.observe_completion("first-message", {"successful_tool_calls": [success()]})
    ledger.observe_completion("second-message", {"failed_tool_calls": [failure()]})
    ledger.observe_completion("first-message", {"successful_tool_calls": [success()]})
    assert len(ledger.effective()) == 1


def test_repeated_snapshots_do_not_duplicate_failure_or_reset_resolution():
    ledger = ToolFailureLedger()
    failed = {"failed_tool_calls": [failure()]}
    ledger.observe_completion("first-message", failed)
    ledger.observe_completion("first-message", failed)
    assert len(ledger.effective()) == 1
    ledger.observe_completion("second-message", {"successful_tool_calls": [success()]})
    ledger.observe_completion("first-message", failed)
    assert ledger.effective() == []


def test_restore_retains_unresolved_failures_and_format_without_private_parameters():
    ledger = observed_failure("render_document", {"format": "pdf", "document_spec": {"private": "secret business text"}})
    saved = ledger.state()
    assert "secret business text" not in repr(saved)
    restored = ToolFailureLedger(saved)
    assert restored.effective(completed_delivery())[0]["arguments"] == {"format": "pdf"}


def test_restore_retains_same_tool_resolution():
    ledger = observed_failure("query_business_data")
    ledger.observe_tool("next", "ok", "query_business_data", {}, {"status": "success"})
    restored = ToolFailureLedger(ledger.state())
    assert restored.effective() == []
    assert restored.status()["recovered"]


def test_same_message_native_recovery_requires_recovered_id():
    ledger = ToolFailureLedger()
    ledger.observe_completion("message", {"failed_tool_calls": [failure()], "successful_tool_calls": [success(index=1)]})
    assert ledger.effective()
    ledger.observe_completion("message", {"recovered_tool_calls": [failure()], "successful_tool_calls": [success(index=3)]})
    assert ledger.effective() == []


def test_actual_observed_tool_order_recovers_same_message_failure():
    ledger = observed_failure("query_business_data")
    ledger.observe_tool("first-message", "ok", "query_business_data", {}, {"status": "success"})
    assert ledger.effective() == []
    ledger.observe_tool("first-message", "failed-again", "query_business_data", {}, {"status": "failed"})
    assert [row["call_id"] for row in ledger.effective()] == ["failed-again"]


def test_native_snapshot_cannot_overwrite_authoritative_failed_receipt():
    ledger = observed_failure("query_business_data")
    ledger.observe_completion("first-message", {"successful_tool_calls": [success(identity="failed")]})
    assert ledger.effective()


@pytest.mark.parametrize(("name", "args", "required", "files"), [
    ("render_document", {"format": "docx"}, {"docx": True}, [{"id": "word", "kind": "docx"}]),
    ("render_document", {"format": "pdf"}, {"pdf": True}, [{"id": "pdf", "kind": "pdf"}]),
    ("render_spreadsheet", {}, {"xlsx": True}, [{"id": "excel", "kind": "xlsx"}]),
    ("export_query_spreadsheet", {}, {"xlsx": True}, [{"id": "excel", "kind": "xlsx"}]),
    ("finalize_presentation", {}, {"pptx": 15}, [{"id": "ppt", "kind": "pptx", "pages": 15}]),
    ("write_presentation_slides", {}, {"pptx": 15}, [{"id": "ppt", "kind": "pptx", "pages": 15}]),
    ("render_chart_image", {"chart_type": "pie"}, {"chart": True, "chart_types": ["pie"]},
     [{"id": "chart", "kind": "chart", "chart_types": ["pie"]}]),
])
def test_explicit_generation_failures_can_be_replaced_by_matching_real_delivery(name, args, required, files):
    ledger = observed_failure(name, args)
    status = ledger.status(completed_delivery(required, files))
    assert status["unresolved"] == []
    assert status["replaced"][0]["resolution"] == "validated_artifact_replacement"
    assert ledger.effective(), "产物替代不删除原始失败记录"


def test_word_cannot_replace_failed_pdf_export():
    ledger = observed_failure("render_document", {"format": "pdf"})
    assert ledger.effective(completed_delivery())


def test_unknown_document_format_from_completion_is_not_assumed_to_be_word():
    ledger = ToolFailureLedger()
    ledger.observe_completion("message", {"failed_tool_calls": [failure("render_document")]})
    assert ledger.effective(completed_delivery())


@pytest.mark.parametrize("changes", [{"missing": ["Word 文件"]}, {"blocker": "数据库超时"},
    {"blocker": "没有导出权限"}, {"files": []}, {"files": [{"kind": "docx"}]}])
def test_claimed_completion_without_validated_files_or_with_blocker_is_not_a_replacement(changes):
    assert observed_failure("render_document").effective(completed_delivery(**changes))


def test_incorrect_page_count_does_not_replace_failed_ppt_creation():
    ledger = observed_failure("finalize_presentation")
    state = completed_delivery({"pptx": 15}, [{"id": "ppt", "kind": "pptx", "pages": 14}])
    assert ledger.effective(state)


def test_unrelated_chart_type_cannot_replace_failed_pie():
    ledger = observed_failure("render_chart_image", {"chart_type": "pie"})
    state = completed_delivery({"chart": True}, [{"id": "bar", "kind": "chart", "chart_types": ["bar"]}])
    assert ledger.effective(state)


def test_inspection_export_needs_all_relevant_requested_files():
    ledger = observed_failure("export_inspection_report")
    required = {"docx": True, "xlsx": True, "chart": True, "chart_types": ["pie"]}
    files = [{"id": "word", "kind": "docx"}, {"id": "excel", "kind": "xlsx"},
             {"id": "chart", "kind": "chart", "chart_types": ["pie"]}]
    assert ledger.effective(completed_delivery(required, files[:-1]))
    assert ledger.effective(completed_delivery(required, files)) == []


def test_export_failure_cannot_use_an_unrequested_file_as_substitute():
    ledger = observed_failure("render_spreadsheet")
    state = completed_delivery(files=[{"id": "word", "kind": "docx"}, {"id": "excel", "kind": "xlsx"}])
    assert ledger.effective(state)


def test_invalid_snapshot_rows_and_nonterminal_tools_are_ignored():
    ledger = ToolFailureLedger({"calls": [None, {}, {"outcome": "failed"}]})
    ledger.observe_tool("message", "running", "query_business_data", {}, {"status": "running"})
    ledger.observe_completion("message", {"failed_tool_calls": [None, {}]})
    assert ledger.state()["calls"] == []
