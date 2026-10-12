"""用真实查询、Node 解码及工具落库复现大整数跨语言传输，业务库和模型均不参与。"""
from __future__ import annotations

from io import StringIO
from copy import deepcopy
import json
import subprocess

import pytest
from sqlalchemy import select

from app.business_worker import chat, tools
from app.business_worker.protocol import JsonRpc
from app.models import Message, ToolCall
from app.modules.analytics.execution import json_value
from app.modules.analytics.models import QueryHistory
from app.providers.tool.base import ToolResult
from app.providers.tool.gateway import ToolGateway
from test_analytics import API, run_sql, source
from test_business_worker import prepare, worker_context


BIG = 9007199254740993


def javascript_parse(text):
    """使用实际 Node JSON.parse 验证浏览器/宿主的数值语义，不以 Python 解码代替。"""
    result = subprocess.run(
        ["node", "-e", "process.stdout.write(JSON.stringify(JSON.parse(require('node:fs').readFileSync(0,'utf8'))))"],
        input=text, text=True, encoding="utf-8", capture_output=True, timeout=5, check=True,
    )
    return json.loads(result.stdout)


def rpc_packet(value):
    """复用正式协议编码；空输入结束服务并回收全部执行器。"""
    sink = StringIO()
    rpc = JsonRpc(StringIO(), sink)
    try:
        rpc.write({"jsonrpc": "2.0", "id": "fixture", "result": value})
        return sink.getvalue()
    finally:
        rpc.serve(lambda *_: None)


@pytest.mark.parametrize("value, expected", [
    (BIG, str(BIG)), (-BIG, str(-BIG)), (2**53, str(2**53)),
    (2**53 - 1, 2**53 - 1), (-(2**53 - 1), -(2**53 - 1)),
    (0, 0), (True, True), (False, False), (None, None), ("000123", "000123"),
])
def test_query_value_preserves_unsafe_integer_and_other_scalar_types(value, expected):
    actual = json_value(value)
    assert actual == expected and type(actual) is type(expected)


@pytest.mark.parametrize("value", [BIG, -BIG, 9223372036854775807])
def test_real_query_http_survives_javascript_parse(client, administrator, source, value):
    response = run_sql(client, administrator, source,
        f"SELECT {value} AS amount FROM sales LIMIT 1", needAnalysis=False, needChart=False)
    assert response.status_code == 200, response.text
    result = javascript_parse(response.text)["data"]
    assert result["data"] == [{"amount": str(value)}]
    assert result["columns"][0]["dataType"] == "bigint"


def test_old_query_result_is_encoded_without_requery_or_overwriting_evidence(client, administrator, source, db, monkeypatch):
    created = run_sql(client, administrator, source, "SELECT amount FROM sales LIMIT 1",
        needAnalysis=False, needChart=False).json()["data"]
    record = db.get(QueryHistory, created["queryId"])
    record.result_json = {**record.result_json, "data": [{"amount": BIG}]}
    db.commit()
    from app.modules.analytics import queries
    monkeypatch.setattr(queries, "execute_read", lambda *_args, **_kwargs: pytest.fail("历史读取不得重新查询"))
    response = client.get(API + "/history/" + record.id, headers=administrator)
    assert response.status_code == 200, response.text
    assert javascript_parse(response.text)["data"]["data"] == [{"amount": str(BIG)}]
    db.expire_all()
    assert db.get(QueryHistory, record.id).result_json["data"] == [{"amount": BIG}]


def test_rpc_nested_numbers_keep_exact_text_but_not_coerce_boolean_or_identifiers():
    value = {"rows": [{"large": BIG, "small": 7, "active": True, "code": "000123"}], "array": [BIG, -BIG]}
    result = javascript_parse(rpc_packet(value))["result"]
    assert result == {"rows": [{"large": str(BIG), "small": 7, "active": True, "code": "000123"}],
                      "array": [str(BIG), str(-BIG)]}
    assert value["rows"][0]["large"] == BIG, "编码不能修改调用方对象"


def test_tool_receipt_and_chat_projection_keep_exact_integer_after_node_roundtrip(worker_context, db, monkeypatch):
    prepared = prepare(worker_context, "读取当前文件信息")
    monkeypatch.setattr(ToolGateway, "execute", lambda *_args, **_kwargs:
        ToolResult(status="success", data={"records": [{"amount": BIG, "enabled": True}]}))
    receipt = tools.execute(prepared["run_id"], "numeric-result", 71, "list_files", {})
    assert receipt["status"] == "success", receipt
    wire = javascript_parse(rpc_packet(receipt))["result"]
    chat.append_event(prepared["run_id"], "tool.completed", {
        "call_id": "numeric-result", "tool_name": "list_files", "result": wire,
    }, native_sequence=72)
    db.expire_all()
    call = db.scalar(select(ToolCall).where(ToolCall.run_id == prepared["run_id"]))
    result_part = next(part for part in db.get(Message, prepared["message_id"]).parts_json
        if part.get("id") == "numeric-result" and part["type"] == "tool_result")
    expected = {"records": [{"amount": str(BIG), "enabled": True}]}
    assert call.result_json["data"] == expected
    assert result_part["result"]["data"] == expected


def test_saved_report_and_encoded_chart_data_keep_integer_through_both_json_layers(worker_context, db):
    from test_dsh_dashboard_delivery import saved_dashboard
    from app.modules.reports.service import report_view
    prepared = prepare(worker_context, "生成数字大屏")
    _, report = saved_dashboard(db, prepared["run_id"], rows=[{"应收金额": BIG}])
    content = deepcopy(report.content)
    content["widgets"][0]["config"]["chartData"] = json.dumps([{"应收金额": BIG}], ensure_ascii=False)
    report.content = content
    db.commit()
    received = javascript_parse(report_view(report).content)
    config = received["widgets"][0]["config"]
    assert config["chartDataParsed"] == [{"应收金额": str(BIG)}]
    assert javascript_parse(config["chartData"]) == [{"应收金额": str(BIG)}]
    db.expire_all()
    assert report.content["widgets"][0]["config"]["chartDataParsed"] == [{"应收金额": BIG}]


@pytest.mark.parametrize("original, encoded, expected_status", [
    (BIG, str(BIG), "completed"),
    (BIG, str(BIG - 1), "partial"),
    (1, "1", "partial"),
    (BIG, "0" + str(BIG), "partial"),
])
def test_dashboard_legacy_wire_representation_does_not_waive_original_value_evidence(
        worker_context, db, original, encoded, expected_status):
    from test_dsh_dashboard_delivery import saved_dashboard
    prepared = prepare(worker_context, "生成数字大屏")
    _, report = saved_dashboard(db, prepared["run_id"], rows=[{"应收金额": original}])
    content = deepcopy(report.content)
    content["widgets"][0]["config"]["chartDataParsed"] = [{"应收金额": encoded}]
    report.content = content
    db.commit()
    settled = chat.settle(prepared["run_id"], "completed", "已保存")
    assert settled["status"] == expected_status, settled


def test_catalog_payload_normalizes_only_exact_unsafe_integer_representation():
    from app.modules.reports.source_reports import CatalogSnapshotProof
    original = {"id": "sample", "component": "JTable", "config": {
        "chartData": json.dumps([{"amount": BIG}]), "chartDataParsed": [{"amount": BIG, "small": 1}]}}
    encoded = deepcopy(original)
    encoded["config"]["chartData"] = json.dumps([{"amount": str(BIG)}], ensure_ascii=False)
    encoded["config"]["chartDataParsed"][0]["amount"] = str(BIG)
    assert CatalogSnapshotProof.payload(original) == CatalogSnapshotProof.payload(encoded)
    forged = deepcopy(encoded)
    forged["config"]["chartDataParsed"][0]["amount"] = str(BIG - 1)
    assert CatalogSnapshotProof.payload(original) != CatalogSnapshotProof.payload(forged)
    forged = deepcopy(encoded)
    forged["config"]["chartDataParsed"][0]["small"] = "1"
    assert CatalogSnapshotProof.payload(original) != CatalogSnapshotProof.payload(forged)


@pytest.mark.parametrize("endpoint", ["detail", "list"])
def test_old_query_parameters_keep_exact_value_for_browser_reuse(client, administrator, source, db, endpoint):
    created = run_sql(client, administrator, source, "SELECT amount FROM sales WHERE amount < :ceiling",
        parameters={"ceiling": BIG}, needAnalysis=False, needChart=False).json()["data"]
    record = db.get(QueryHistory, created["queryId"])
    original = {"ceiling": BIG}
    record.result_json = {**record.result_json, "parameters": original}
    db.commit()
    url = API + "/history/" + (record.id if endpoint == "detail" else "recent")
    response = client.get(url, headers=administrator)
    assert response.status_code == 200, response.text
    received = javascript_parse(response.text)["data"]
    item = received if endpoint == "detail" else next(row for row in received if row["id"] == record.id)
    assert item["parameters"] == {**original, "ceiling": str(BIG)}
    db.expire_all()
    assert db.get(QueryHistory, record.id).result_json["parameters"] == original


@pytest.mark.usefixtures("approved_report_semantics")
def test_old_revision_content_and_nested_chart_data_keep_exact_values(client, administrator, db):
    from test_reports import REPORTS, create_report
    from app.modules.reports.models import ReportRevision
    created = create_report(client, administrator)
    revision = db.scalar(select(ReportRevision).where(ReportRevision.report_id == created["id"]))
    snapshot = deepcopy(revision.snapshot)
    content = json.loads(snapshot["content"])
    content["widgets"][0]["config"].update({
        "chartDataParsed": [{"amount": BIG}], "chartData": json.dumps([{"amount": BIG}]),
    })
    snapshot["content"] = json.dumps(content, ensure_ascii=False)
    revision.snapshot = snapshot
    db.commit()
    response = client.get(REPORTS + "/" + created["id"] + "/revisions", headers=administrator)
    assert response.status_code == 200, response.text
    received = javascript_parse(response.text)["data"][0]["snapshot"]
    config = javascript_parse(received["content"])["widgets"][0]["config"]
    assert config["chartDataParsed"] == [{"amount": str(BIG)}]
    assert javascript_parse(config["chartData"]) == [{"amount": str(BIG)}]
    db.expire_all()
    assert db.get(ReportRevision, revision.id).snapshot == snapshot
