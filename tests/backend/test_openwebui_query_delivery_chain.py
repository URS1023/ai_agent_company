"""贯通真实内置工具：同一查询生成饼图、Excel、大屏、图片及嵌图宋体 Word。"""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl import load_workbook
from PIL import Image
from sqlalchemy import select

from test_openwebui_query_dashboard import dashboard_case

from app.common.storage import get_storage
from app.config import settings
from app.models import Artifact, ToolCall
from app.modules.analytics.schemas import QueryResult
from app.modules.openwebui.artifact_inspection import inspect_file
from app.modules.openwebui.artifacts import persist_artifacts
from app.modules.openwebui.delivery import DeliveryTracker
from app.modules.reports.models import Report
from app.modules.runtime import service as runtime
from app.providers.tool import builtin_provider
from app.providers.tool.base import ToolResult


def test_same_query_delivers_verified_chart_excel_dashboard_and_embedded_word(dashboard_case, tmp_path, monkeypatch):
    """真实保存所有工件与工具回执，任何模型调用或业务查询均由共享夹具禁止。"""
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    case = dashboard_case
    rows = [{"状态": "已巡检", "设备数": 7}, {"状态": "未巡检", "设备数": 3}]
    request = "做饼图、Excel和Word，再创建大屏放入Word"
    case.run.context_snapshot = {**case.run.context_snapshot, "user_request": request}
    query_result = QueryResult(
        queryId=case.query.id, runId=case.query.run_id, generatedSql=case.query.generated_sql,
        queryType="natural_language", executionTime=1, data=rows, totalCount=2, truncated=False,
        columns=[{"columnName": "状态", "dataType": "varchar", "comment": "巡检状态"},
                 {"columnName": "设备数", "dataType": "integer", "comment": "设备数量（台）"}],
        datasourceId=case.source.id, datasourceVersion=1,
        analysis="隔离验收合成数据：已巡检7台，未巡检3台；共10台，未巡检占30%。",
    )
    case.query.result_json = query_result.model_dump(mode="json")
    # 查询只由合成夹具提供；也经正式回执函数登记，后续工具必须找到本轮真实保存的 queryId。
    case.ctx.db.delete(case.call)
    query_call = runtime.record_tool_call(case.ctx.db, workspace_id=case.ctx.workspace_id, run_id=case.run.id,
        step_id=None, tool_name="query_business_data", tool_version_id=None,
        arguments={"datasource_id": case.source.id, "query": "合成巡检统计"})
    query_receipt = ToolResult(status="success", data=query_result.model_dump(mode="json"))
    runtime.finish_tool_call(case.ctx.db, query_call, query_receipt.to_dict())
    case.ctx.db.commit()

    tracker = DeliveryTracker(request)
    assert {"chart", "chart_types", "xlsx", "docx", "dashboard", "embedded_dashboard"}.issubset(tracker.required)
    assert tracker.required["chart_types"] == ["pie"]
    tracker.observe(case.ctx.db, case.run, "query_business_data", query_call.arguments_json, query_receipt)
    assert tracker.status()["missing"]
    specs = {spec.name: spec for spec in builtin_provider.builtin_specs()}
    results = {}
    artifact_cache = {}

    def invoke(name: str, args: dict) -> ToolResult:
        """走实际内置分派函数和正式调用落库函数，交付核验只读取成功回执。"""
        call = runtime.record_tool_call(case.ctx.db, workspace_id=case.ctx.workspace_id, run_id=case.run.id,
            step_id=None, tool_name=name, tool_version_id=None, arguments=args)
        case.ctx.db.commit()
        result = builtin_provider.invoke(specs[name], args, case.tool)
        assert result.status == "success", result.to_dict()
        # 与原生后端适配器保持相同流程：把 artifact_id 解析为真实工件引用，再持久化回执。
        persist_artifacts(case.ctx.db, case.run, name, result, artifact_cache)
        runtime.finish_tool_call(case.ctx.db, call, result.to_dict(),
                                 error_code=result.error_code, error_summary=result.error_summary)
        case.ctx.db.commit()
        tracker.observe(case.ctx.db, case.run, name, args, result)
        results[name] = result
        return result

    chart = invoke("render_chart_image", {
        "query_id": "query", "chart_type": "pie", "title": "设备巡检状态分布",
        "label_column": "状态", "value_column": "设备数",
    })
    sheet = invoke("export_query_spreadsheet", {"query_id": "query"})
    dashboard = invoke("create_dashboard_from_queries", {"query_ids": ["query"], "title": "设备巡检数字大屏"})
    dashboard_image = invoke("render_dashboard_image", {"report_id": dashboard.data["reportId"]})
    assert "Word 文件" in tracker.status()["missing"]
    assert any("Word 中嵌入" in item for item in tracker.status()["missing"])
    word = invoke("render_document", {
        "format": "docx", "name": "设备巡检分析报告",
        "document_spec": {"title": "设备巡检分析报告", "sections": [
            {"title": "查询范围与统计结果", "blocks": [
                {"type": "p", "text": query_result.analysis},
                {"type": "table", "headers": ["状态", "设备数"], "rows": rows},
                {"type": "image", "artifact_id": chart.artifacts[0]["id"], "caption": "巡检状态：7台已巡检，3台未巡检"},
            ]},
            {"title": "数字大屏数据快照", "blocks": [
                {"type": "p", "text": "同一成功查询的保存结果，不是实时刷新。查询标识：query。"},
                {"type": "image", "artifact_id": dashboard_image.artifacts[0]["id"], "caption": "已保存大屏的真实数据快照"},
            ]},
        ]},
    })

    def stored(result: ToolResult) -> tuple[Artifact, bytes]:
        """从实际工件表与临时存储取回结果，禁止用工具输出中的声明代替文件检查。"""
        artifact = case.ctx.db.get(Artifact, result.artifacts[0]["id"])
        assert artifact.workspace_id == case.ctx.workspace_id and artifact.owner_user_id == case.ctx.user_id
        assert artifact.run_id == case.run.id and artifact.status == "ready"
        path = Path(get_storage().get_path(artifact.storage_key))
        assert path.is_relative_to(tmp_path.resolve())
        data = path.read_bytes()
        assert sha256(data).hexdigest() == artifact.sha256
        return artifact, data

    chart_row, chart_bytes = stored(chart)
    proof = chart_row.metadata_json["delivery_evidence"]
    assert proof["source_query_id"] == "query" and proof["chart_types"] == ["pie"]
    assert proof["source_datasource_id"] == "source" and proof["source_rows"] == 2
    assert chart_row.metadata_json["font"] == "宋体"
    expected_values = json.dumps({"labels": ["已巡检", "未巡检"], "values": [7.0, 3.0]},
                                ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    assert proof["rendered_values_sha256"] == sha256(expected_values).hexdigest()
    with Image.open(BytesIO(chart_bytes)) as image:
        assert image.format == "PNG" and image.width > 1000 and image.height > 700
        assert all(high - low > 100 for low, high in image.convert("RGB").getextrema())

    _, sheet_bytes = stored(sheet)
    workbook = load_workbook(BytesIO(sheet_bytes), data_only=False)
    assert list(workbook["查询结果"].values) == [("状态", "设备数"), ("已巡检", 7), ("未巡检", 3)]
    origin = dict(workbook["数据来源"].values)
    assert origin["查询标识"] == "query" and origin["数据源标识"] == "source"
    assert origin["样本行数"] == 2 and origin["已截断"] == "False"
    assert origin["实际 SQL"] == case.query.generated_sql
    for worksheet in workbook.worksheets:
        for row in worksheet:
            assert all(cell.font.name == "宋体" for cell in row if cell.value is not None)

    report = case.ctx.db.get(Report, dashboard.data["reportId"])
    widgets = [widget for widget in report.content["widgets"] if widget.get("dataStatus") == "verified"]
    assert len(widgets) == 1
    assert widgets[0]["data"] == widgets[0]["config"]["chartDataParsed"] == rows
    assert widgets[0]["config"]["queryId"] == "query" and widgets[0]["config"]["dataType"] == 1
    assert report.refresh_seconds == 0
    image_row, image_bytes = stored(dashboard_image)
    assert image_row.metadata_json["delivery_evidence"]["dashboard_id"] == report.id
    assert image_row.metadata_json["delivery_evidence"]["source_query_ids"] == ["query"]
    image_digest = sha256(image_bytes).hexdigest()

    word_row, word_bytes = stored(word)
    inspection = inspect_file(word_row.name, word_bytes)
    assert inspection["kind"] == "docx"
    assert image_digest in inspection["images"]
    assert sha256(chart_bytes).hexdigest() in inspection["images"]
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with ZipFile(BytesIO(word_bytes)) as package:
        for name in ("word/document.xml", "word/styles.xml"):
            document = ElementTree.fromstring(package.read(name))
            fonts = list(document.iter(namespace + "rFonts"))
            assert fonts
            for font in fonts:
                assert all(font.get(namespace + attribute) == "宋体" for attribute in ("ascii", "hAnsi", "eastAsia", "cs"))
        document = ElementTree.fromstring(package.read("word/document.xml"))
        actual_text = "".join(element.text or "" for element in document.iter(namespace + "t"))
        assert "已巡检7台，未巡检3台" in actual_text and "未巡检占30%" in actual_text

    state = tracker.status()
    assert state["missing"] == [] and state["rejected_files"] == []
    assert image_digest in tracker.dashboard_images
    assert {item["kind"] for item in state["files"]} == {"chart", "xlsx", "dashboard_image", "docx"}
    calls = case.ctx.db.scalars(select(ToolCall)).all()
    assert len(calls) == 6 and all(call.status == "completed" and call.result_json["status"] == "success" for call in calls)
    assert [call for call in calls if call.tool_name == "query_business_data"] == [query_call]
