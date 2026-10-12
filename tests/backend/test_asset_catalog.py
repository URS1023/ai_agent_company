"""Real asset aggregation, semantic governance and permission boundaries.

中文说明：资产目录：真实聚合已授权资源（不含密钥、不做在线探测）、继承当前文件分类并支持标注覆盖、
别名/术语/指标/血缘仅在有效可见源上生成、并遵守源权限与元数据修改权限。
隔离方式：client 夹具（内存 SQLite + TestClient），真实落库与查询，不连业务库。
"""
from datetime import datetime

from sqlalchemy import select

from app.ids import new_id
from app.models import DataSource, File, KnowledgeBase, WorkspaceMember
from app.modules.analytics.models import SourceMetadata
from app.modules.files.models import FileCategory, FileMetadata
from app.modules.reports.models import Report
from test_system_foundation import activate_user, create_role, create_user


API = "/api/v1/assets"


def result(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def seed_resources(db, headers):
    workspace_id = headers["X-Workspace-Id"]
    owner_id = db.scalar(select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == workspace_id))
    now = datetime.utcnow()
    source = DataSource(
        id=new_id(), workspace_id=workspace_id, owner_user_id=owner_id, name="财务生产库", provider="sqlite",
        connection_config={"version": 1, "databaseName": "finance", "password": "must-never-leak"},
        status="active", visibility="workspace", created_at=now, updated_at=now,
    )
    file = File(
        id=new_id(), workspace_id=workspace_id, owner_user_id=owner_id, name="设备说明书.pdf",
        mime_type="application/pdf", size=128, storage_key="tests/" + new_id(), sha256="a" * 64,
        source_type="upload", status="ready", created_at=now,
    )
    knowledge = KnowledgeBase(
        id=new_id(), workspace_id=workspace_id, owner_user_id=owner_id, name="设备知识库",
        description="设备维护资料", visibility="workspace", status="active", created_at=now, updated_at=now,
    )
    report = Report(
        workspace_id=workspace_id, owner_user_id=owner_id, name="财务与设备大屏", description="经营及设备状态",
        datasource_ids=[source.id], content={"components": [{"id": "sales"}, {"id": "alarm"}]},
        status="draft", template_mode="manual", template_id=None,
    )
    metadata = SourceMetadata(
        workspace_id=workspace_id, owner_user_id=owner_id, datasource_id=source.id, source_version=1,
        status="ready", error_message="", tables_json=[{
            "tableName": "orders", "tableComment": "订单", "tableType": "TABLE", "rowCount": None,
            "foreignKeys": [], "columns": [
                {"columnName": "amount", "dataType": "DECIMAL", "comment": "原始金额", "nullable": False, "primaryKey": False},
                {"columnName": "device_code", "dataType": "VARCHAR", "comment": "设备编码", "nullable": False, "primaryKey": False},
            ],
        }],
    )
    db.add_all([source, file, knowledge, report, metadata])
    db.commit()
    return source, file, knowledge, report


def test_catalog_aggregates_real_authorized_resources_without_secrets_or_live_probe(client, administrator, db):
    source, file, knowledge, report = seed_resources(db, administrator)
    types = result(client.get(API + "/types", headers=administrator))
    counts = {item["type"]: item["count"] for item in types}
    assert counts["datasource"] == 1 and counts["file"] == 1 and counts["report"] == 1

    catalog = result(client.get(API + "/catalog", headers=administrator))
    names = {item["name"] for item in catalog["items"]}
    assert {source.name, file.name, knowledge.name, report.name}.issubset(names)
    assert "must-never-leak" not in str(catalog)

    detail = result(client.get(f"{API}/catalog/datasource/{source.id}", headers=administrator))
    assert detail["details"]["provider"] == "sqlite"
    assert detail["metadata"]["status"] == "ready"
    assert {item["path"] for item in detail["fields"]} == {"orders.amount", "orders.device_code"}
    assert "connection_config" not in detail and "must-never-leak" not in str(detail)


def test_catalog_inherits_current_file_category_and_allows_annotation_override(client, administrator, db):
    _, file, _, _ = seed_resources(db, administrator)
    workspace_id = administrator["X-Workspace-Id"]
    owner_id = db.scalar(select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == workspace_id))
    parent = FileCategory(id=new_id(), workspace_id=workspace_id, name="设备资料", description="设备资料根目录")
    child = FileCategory(
        id=new_id(), workspace_id=workspace_id, parent_id=parent.id, name="泵站手册", description="泵站资料",
    )
    metadata = FileMetadata(
        workspace_id=workspace_id,
        file_id=file.id,
        logical_id=file.id,
        category_id=child.id,
        description="主泵运行与维护说明",
        tags=["主泵", "维护"],
        revision_number=2,
        is_current=True,
        previous_file_id=None,
        change_note="测试当前版本",
        created_by_user_id=owner_id,
    )
    historic_file = File(
        id=new_id(), workspace_id=workspace_id, owner_user_id=owner_id, name="设备说明书-旧版本.pdf",
        mime_type="application/pdf", size=64, storage_key="tests/" + new_id(), sha256="b" * 64,
        source_type="upload", status="ready", created_at=datetime.utcnow(),
    )
    historic_metadata = FileMetadata(
        workspace_id=workspace_id,
        file_id=historic_file.id,
        logical_id=file.id,
        category_id=child.id,
        description="不应出现在目录中的旧版本",
        tags=[],
        revision_number=1,
        is_current=False,
        previous_file_id=None,
        change_note="测试历史版本",
        created_by_user_id=owner_id,
    )
    db.add_all([parent, child, metadata, historic_file, historic_metadata])
    db.commit()

    catalog = result(client.get(
        API + "/catalog",
        headers=administrator,
        params={"type": "file", "keyword": "设备说明书"},
    ))
    assert [item["id"] for item in catalog["items"]] == [file.id]
    assert catalog["items"][0]["category"] == child.name
    assert catalog["items"][0]["description"] == metadata.description

    native_filtered = result(client.get(
        API + "/catalog", headers=administrator, params={"type": "file", "category": child.name},
    ))
    assert [item["id"] for item in native_filtered["items"]] == [file.id]
    detail = result(client.get(f"{API}/catalog/file/{file.id}", headers=administrator))
    assert detail["category"] == child.name
    assert detail["nativeDescription"] == metadata.description

    result(client.put(
        f"{API}/catalog/file/{file.id}/annotation",
        headers=administrator,
        json={"category": "受控资料", "description": "治理口径覆盖原生说明"},
    ))
    native_filtered = result(client.get(
        API + "/catalog", headers=administrator, params={"type": "file", "category": child.name},
    ))
    governed_filtered = result(client.get(
        API + "/catalog", headers=administrator, params={"type": "file", "category": "受控资料"},
    ))
    assert native_filtered["items"] == []
    assert [item["id"] for item in governed_filtered["items"]] == [file.id]
    assert governed_filtered["items"][0]["description"] == "治理口径覆盖原生说明"


def test_annotation_alias_search_field_docs_freshness_and_version_conflict(client, administrator, db):
    source, _, _, _ = seed_resources(db, administrator)
    annotated = result(client.put(f"{API}/catalog/datasource/{source.id}/annotation", headers=administrator, json={
        "aliases": ["财务主库", "经营数据"], "category": "核心数据", "businessDomain": "财务",
        "description": "经营分析正式口径", "tags": ["生产"], "sensitivity": "confidential",
        "freshnessSeconds": 3600, "fieldDescriptions": {"orders.amount": "含税订单金额"},
    }))
    assert annotated["annotation"]["version"] == 1
    assert annotated["fields"][0]["description"] == "含税订单金额"
    assert annotated["freshness"]["state"] == "current"
    searched = result(client.get(API + "/catalog?keyword=" + "财务主库", headers=administrator))
    assert [item["id"] for item in searched["items"]] == [source.id]

    stale = client.put(f"{API}/catalog/datasource/{source.id}/annotation", headers=administrator, json={
        "aliases": [], "version": 99,
    })
    assert stale.status_code == 409
    current = annotated["annotation"]
    archived = result(client.delete(
        f"{API}/catalog/datasource/{source.id}/annotation?version={current['version']}", headers=administrator,
    ))
    assert archived["deleted"] is True
    assert result(client.get(f"{API}/catalog/datasource/{source.id}", headers=administrator))["category"] == ""


def test_terms_metrics_and_lineage_use_valid_visible_sources(client, administrator, db):
    source, file, _, _ = seed_resources(db, administrator)
    term = result(client.post(API + "/terms", headers=administrator, json={
        "code": "GMV", "name": "成交总额", "definition": "订单含税金额合计", "aliases": ["交易额"],
        "businessDomain": "财务",
    }), 201)
    assert client.put(API + f"/terms/{term['id']}", headers=administrator, json={
        "code": "GMV", "name": "旧口径", "definition": "错误", "version": term["version"] + 9,
    }).status_code == 409
    term = result(client.put(API + f"/terms/{term['id']}", headers=administrator, json={
        "code": "GMV", "name": "成交总额", "definition": "已支付订单含税金额合计",
        "aliases": ["交易额"], "businessDomain": "财务", "version": term["version"],
    }))
    assert term["version"] == 2

    metric = result(client.post(API + "/metrics", headers=administrator, json={
        "code": "ORDER_AMOUNT", "name": "订单金额", "description": "经营指标",
        "formula": "SUM(orders.amount)", "aggregation": "sum", "unit": "元", "granularity": "日",
        "dimensions": ["device_code"], "sourceRefs": [{"type": "datasource", "id": source.id, "field": "orders.amount"}],
    }), 201)
    assert metric["sourceRefs"][0]["name"] == source.name
    assert client.post(API + "/metrics", headers=administrator, json={
        "code": "INVALID_SOURCE", "name": "无效指标", "formula": "SUM(x)",
        "sourceRefs": [{"type": "datasource", "id": "missing"}],
    }).status_code == 404

    relation = result(client.post(API + "/relations", headers=administrator, json={
        "source": {"type": "datasource", "id": source.id},
        "target": {"type": "file", "id": file.id},
        "relationKind": "related_to", "description": "字段口径说明",
    }), 201)
    listed = result(client.get(API + "/relations", headers=administrator))
    assert listed["total"] == 1 and listed["items"][0]["source"]["name"] == source.name
    assert client.post(API + "/relations", headers=administrator, json={
        "source": {"type": "file", "id": file.id}, "target": {"type": "file", "id": file.id},
        "relationKind": "related_to",
    }).status_code == 422
    result(client.delete(f"{API}/relations/{relation['id']}?version={relation['version']}", headers=administrator))
    assert result(client.get(f"{API}/catalog/file/{file.id}", headers=administrator))["id"] == file.id


def test_catalog_respects_source_permissions_and_metadata_mutation_permission(client, administrator, db):
    source, file, _, _ = seed_resources(db, administrator)
    role = create_role(
        client, administrator, code="asset_file_viewer",
        permissions=["home.view", "assets.view", "files.view"], scope="workspace",
    )
    create_user(client, administrator, [role["id"]], username="asset-viewer")
    viewer = activate_user(client, username="asset-viewer")
    catalog = result(client.get(API + "/catalog", headers=viewer))
    ids = {item["id"] for item in catalog["items"]}
    assert file.id in ids and source.id not in ids
    assert client.get(f"{API}/catalog/datasource/{source.id}", headers=viewer).status_code == 404
    assert client.put(f"{API}/catalog/file/{file.id}/annotation", headers=viewer, json={"aliases": ["说明书"]}).status_code == 403
