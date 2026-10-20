"""验证真实HTTP身份、CSRF和领域DTO装配；数据库均为本测试新建文件。"""
from fastapi.testclient import TestClient

from agent_platform.core.settings import Settings
from agent_platform.main import create_app


def test_http_database_quality_and_crud(tmp_path):
    settings = Settings(data_dir=tmp_path / "http-data", enabled_modules=("identity", "workspace", "data", "manufacturing", "reports"))
    with TestClient(create_app(settings)) as client:
        initialized = client.post("/api/v1/auth/setup", json={"username": "domain-admin", "password": "Domain-test-password-2026", "display_name": "测试管理员", "workspace_name": "隔离测试车间"})
        assert initialized.status_code == 201, initialized.text
        client.headers["X-CSRF-Token"] = initialized.json()["csrf_token"]
        device = client.post("/api/v1/devices", json={"name": "循环泵", "code": "PUMP-01", "device_type": "离心泵", "location": "一车间"})
        assert device.status_code == 200, device.text
        device = device.json()
        renamed = client.patch(f'/api/v1/devices/{device["id"]}', json={"revision": 1, "name": "循环泵02"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["device_type"] == "离心泵"
        assert client.patch(f'/api/v1/devices/{device["id"]}', json={"revision": 1, "name": "过期编辑"}).status_code == 409
        template = client.post("/api/v1/inspection-templates", json={"name": "温度标准", "target_type": "device", "metrics": [{"id": "temp", "name": "温度", "unit": "℃", "standard_max": "80"}]}).json()
        inspection = client.post("/api/v1/inspections", json={"name": "设备质检", "target_type": "device", "device_id": device["id"], "template_id": template["id"], "template_revision": 1}).json()
        dataset = client.post("/api/v1/datasets", json={"name": "传感器快照", "csv_text": "sample_id,temperature\nS1,75.00\n"})
        assert dataset.status_code == 201, dataset.text
        dataset = dataset.json()
        query = client.post("/api/v1/queries", json={"datasource_id": dataset["datasource_id"], "sql": f'SELECT * FROM "{dataset["table_name"]}"'}).json()
        imported = client.post(f'/api/v1/inspections/{inspection["id"]}/imports', json={"revision": 1, "query_id": query["id"], "mappings": [{"metric_id": "temp", "value_field": "temperature", "unit": "℃"}]})
        assert imported.status_code == 200, imported.text
        run = client.post(f'/api/v1/inspections/{inspection["id"]}/runs', json={"revision": imported.json()["revision"]})
        assert run.status_code == 200, run.text
        assert run.json()["outcome"] == "passed"
        task_id = run.json()["result"]["execution_task_id"]
        assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "waiting_review"
        exported = client.post(f'/api/v1/inspections/{inspection["id"]}/exports')
        assert exported.status_code == 200
        assert "75.00" in exported.text
        assert len(client.get("/api/v1/report-templates").json()["items"]) == 20
        client.headers.pop("X-CSRF-Token")
        assert client.post("/api/v1/products", json={"name": "未带CSRF"}).status_code == 403


def test_failed_maintenance_records_execution_after_business_rollback(tmp_path):
    settings = Settings(data_dir=tmp_path / "failed-http-data", enabled_modules=("identity", "workspace", "data", "manufacturing"))
    with TestClient(create_app(settings)) as client:
        initialized = client.post("/api/v1/auth/setup", json={"username": "failure-admin", "password": "Failure-test-password-2026", "display_name": "管理员", "workspace_name": "隔离异常验证"}).json()
        client.headers["X-CSRF-Token"] = initialized["csrf_token"]
        device = client.post("/api/v1/devices", json={"name": "本测试设备"}).json()
        dataset = client.post("/api/v1/datasets", json={"name": "有限预算", "csv_text": "timestamp,load,value\n2026-01-01T00:00:00Z,10,50\n2026-01-01T00:01:00Z,10,51\n2026-01-01T00:02:00Z,10,52\n"}).json()
        source = client.get(f'/api/v1/datasources/{dataset["datasource_id"]}').json()
        client.patch(f'/api/v1/datasources/{source["id"]}', json={"revision": source["revision"], "max_rows": 1})
        failed = client.post("/api/v1/maintenance-analyses", json={"device_id": device["id"], "datasource_id": source["id"], "sql": f'SELECT * FROM "{dataset["table_name"]}"'})
        assert failed.status_code == 422, failed.text
        assert failed.json()["error"]["code"] == "analysis_source_truncated"
        task_id = failed.json()["error"]["fields"]["execution_task_id"]
        assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "failed"
        assert client.get("/api/v1/maintenance-analyses").json()["total"] == 0
