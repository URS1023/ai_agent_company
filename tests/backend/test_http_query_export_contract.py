"""真实回环 HTTP 留存结果的导出契约：只复用原回执，不重新采集或抄写行数据。"""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

from openpyxl import load_workbook
import pytest

from app.common.storage import get_storage
from app.ids import new_id
from app.models import Artifact, DataSource, Run, ToolCall
from app.modules.analytics.models import QueryHistory
from app.modules.business_ai.context import auth_context_from_tool
from app.modules.chat_artifacts.dashboard_image import dashboard_panels
from app.modules.chat_artifacts.query_spreadsheet import export_query_spreadsheet
from app.modules.system.models import ResourceGrant, Role
from test_collection import API, collect, create_source, upstream
from test_system_foundation import activate_user, create_role, create_user


@pytest.fixture
def saved_http(client, administrator, upstream):
    """只向随机回环桩采集一次；后续所有断言都使用相同的持久 queryId。"""
    source, payload = create_source(client, administrator, upstream,
        fields=[{"name": "设备", "path": "$.device"},
                {"name": "金额", "path": "$.amount", "type": "decimal"},
                {"name": "开机", "path": "$.enabled", "type": "boolean"}])
    response = collect(client, administrator, source)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["success"] and result["queryType"] == "http" and not result["generatedSql"]
    assert len(upstream[1]) == 1
    yield SimpleNamespace(source=source, payload=payload, result=result, requests=upstream[1])
    assert len(upstream[1]) == 1, "历史读取或导出不得重新发送 HTTP"


def export_response(client, headers, query_id, format="xlsx"):
    """通过正式导出接口验证授权、查询历史和文件序列化的完整链路。"""
    return client.post("/api/v1/analytics/export", headers=headers,
                       json={"queryId": query_id, "format": format})


def query_context(db, saved):
    """登记正式查询工具成功回执，让导出走真实运行归属与工具鉴权。"""
    record = db.get(QueryHistory, saved.result["queryId"])
    run = db.get(Run, record.run_id)
    run.context_snapshot = {**run.context_snapshot, "datasource_ids": [record.datasource_id]}
    db.add(ToolCall(id=new_id(), workspace_id=run.workspace_id, run_id=run.id, tool_name="query_business_data",
                   arguments_json={"datasource_id": record.datasource_id, "mode": "natural_language"},
                   result_json={"status": "success", "data": deepcopy(saved.result)}, status="completed"))
    db.commit()
    context = {"db": db, "workspace_id": run.workspace_id, "user_id": run.started_by_user_id, "run_id": run.id}
    return auth_context_from_tool(context), context


def assert_workbook(data, saved):
    """检查实际 XLSX 单元格、列序、来源标识和宋体，不能只看返回成功。"""
    book = load_workbook(BytesIO(data))
    names = [column["columnName"] for column in saved.result["columns"]]
    assert list(book["查询结果"].values) == [tuple(names), *(
        tuple(row.get(name) for name in names) for row in saved.result["data"])]
    origins = dict(book["数据来源"].values)
    assert origins["查询标识"] == saved.result["queryId"]
    assert origins["数据源标识"] == saved.source["id"]
    assert origins["数据源版本"] == saved.source["version"]
    assert origins["已截断"] == "False"
    assert origins["查询类型"] == "HTTP 采集"
    assert "HTTP 采集" in origins["数据范围"]
    for sheet in book:
        assert all(cell.font.name in {"宋体", "SimSun"} for row in sheet for cell in row)


def test_real_http_history_exports_original_xlsx(client, administrator, saved_http):
    response = export_response(client, administrator, saved_http.result["queryId"])
    assert response.status_code == 200, response.text
    assert_workbook(response.content, saved_http)


def test_real_http_query_tool_exports_original_artifact(client, administrator, saved_http, db):
    ctx, context = query_context(db, saved_http)
    result = export_query_spreadsheet(ctx, context,
        {"query_id": saved_http.result["queryId"], "name": "设备采集原始结果.xlsx"})
    assert result.status == "success", result
    artifact = db.get(Artifact, result.data["artifact_id"])
    assert artifact.metadata_json["delivery_evidence"]["source_query_id"] == saved_http.result["queryId"]
    assert_workbook(get_storage().read_bytes(artifact.storage_key), saved_http)


def test_real_http_dashboard_reads_same_authorized_history(client, administrator, saved_http, db):
    ctx, _ = query_context(db, saved_http)
    report = SimpleNamespace(lineage={}, datasource_ids=[saved_http.source["id"]], content={"widgets": [
        {"title": "原采集数据", "data": deepcopy(saved_http.result["data"]),
         "config": {"queryId": saved_http.result["queryId"]}}]})
    panels = dashboard_panels(ctx, report)
    assert panels[0]["rows"] == saved_http.result["data"]
    assert panels[0]["query_id"] == saved_http.result["queryId"]


def test_http_json_export_preserves_request_outcomes_and_origins(client, administrator, saved_http, db):
    """HTTP 扩展证据不能被转换为通用 SQL 结果时无声丢失。"""
    response = export_response(client, administrator, saved_http.result["queryId"], "json")
    assert response.status_code == 200, response.text
    assert response.json() == saved_http.result
    db.expire_all()
    assert db.get(QueryHistory, saved_http.result["queryId"]).result_json == saved_http.result


@pytest.mark.parametrize("change", ["fields", "same_name_path", "disable"])
def test_http_source_edit_invalidates_old_export_without_recollection(client, administrator, saved_http, change):
    """缺少原提取规则时保守拒绝改版后的旧结果，停用也不能冒充版本未变。"""
    payload = deepcopy(saved_http.payload)
    if change == "fields":
        payload["fields"] = payload["fields"][:1]
    elif change == "same_name_path":
        payload["fields"][1]["path"] = "$.device"
    else:
        payload["enabled"] = False
    response = client.put(API + "/sources/" + saved_http.source["id"], headers=administrator,
                          json={**payload, "version": saved_http.source["version"]})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["version"] == saved_http.source["version"] + 1
    denied = export_response(client, administrator, saved_http.result["queryId"])
    assert denied.status_code == 403, denied.text
    assert "来源已变更" in denied.text and "123.450" not in denied.text
    # 采集专属历史仍保留原先的追溯契约，不为了导出放开而改动它。
    history = client.get(API + "/history/" + saved_http.result["queryId"], headers=administrator)
    assert history.status_code == 200 and history.json()["data"] == saved_http.result


@pytest.mark.parametrize("change", ["queryId", "datasourceId", "datasourceVersion", "runId", "extra_row_field",
                                    "duplicate_column", "rowOrigins", "outcomes", "malformed", "current_fields"])
def test_http_export_rejects_mismatched_or_incomplete_evidence(client, administrator, saved_http, db, change):
    record = db.get(QueryHistory, saved_http.result["queryId"])
    value = deepcopy(record.result_json)
    if change in {"queryId", "datasourceId", "runId"}:
        value[change] = "unrelated-record"
    elif change == "datasourceVersion":
        value[change] += 1
    elif change == "extra_row_field":
        value["data"][0]["未授权字段"] = "不能泄漏的值"
    elif change == "duplicate_column":
        value["columns"].append(deepcopy(value["columns"][0]))
    elif change == "rowOrigins":
        value["rowOrigins"] = []
    elif change == "outcomes":
        value["outcomes"][0]["rows"] += 1
    elif change == "malformed":
        value = [value]
    else:
        # 模拟旧版本/人工修复未递增配置版本的情况，仍须核对当前显式提取列。
        source = db.get(DataSource, saved_http.source["id"])
        source.connection_config = {**source.connection_config, "fields": saved_http.payload["fields"][:1]}
    record.result_json = value
    db.commit()
    response = export_response(client, administrator, record.id)
    assert response.status_code in {403, 422}, response.text
    assert "不能泄漏的值" not in response.text and "123.450" not in response.text
    db.expire_all()
    assert db.get(QueryHistory, record.id).result_json == value


@pytest.mark.parametrize("revoked", ["export_permission", "source_grant", "query_owner"])
def test_http_export_rechecks_current_permissions_and_ownership(client, administrator, saved_http, db, revoked):
    """先证明授权用户可导出，再逐项撤权，防止用默认拒绝冒充权限覆盖。"""
    role = create_role(client, administrator, "http-exporter", ["analytics.export", "datasources.view"], "self")
    user = create_user(client, administrator, [role["id"]])
    headers = activate_user(client)
    record = db.get(QueryHistory, saved_http.result["queryId"])
    original_owner = record.owner_user_id
    record.owner_user_id = user["id"]
    share = ResourceGrant(workspace_id=record.workspace_id, resource_type="datasources",
                          resource_id=record.datasource_id, subject_type="user", subject_id=user["id"], actions=["view"])
    db.add(share)
    db.commit()
    baseline = export_response(client, headers, record.id)
    assert baseline.status_code == 200, baseline.text
    if revoked == "source_grant":
        db.delete(share)
    elif revoked == "query_owner":
        record.owner_user_id = original_owner
    else:
        db.get(Role, role["id"]).permissions = ["datasources.view"]
    db.commit()
    denied = export_response(client, headers, record.id)
    assert denied.status_code in {403, 404}, denied.text
    assert "123.450" not in denied.text


def test_partial_http_batch_remains_in_collection_history(client, administrator, upstream):
    """达到有界行数后未执行的请求保持 partial，导出不将它升级为完整成功。"""
    source, _ = create_source(client, administrator, upstream, method="POST", bodies=[{}, {}], maxRows=1)
    response = collect(client, administrator, source, confirmed=True)
    assert response.status_code == 207, response.text
    result = response.json()["data"]
    assert result["status"] == "partial" and result["success"] is False
    assert result["outcomes"][1]["status"] == "skipped"
    denied = export_response(client, administrator, result["queryId"])
    assert denied.status_code == 422, denied.text
    history = client.get(API + "/history/" + result["queryId"], headers=administrator)
    assert history.status_code == 200 and history.json()["data"] == result
    assert len(upstream[1]) == 1
