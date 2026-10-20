"""检验名称投影无需主数据模块权限，且不得越工作区或改写历史标准。"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor, get_actor
from agent_platform.core.database import Base
from agent_platform.core.settings import Settings
from agent_platform.main import create_app
from agent_platform.modules.manufacturing import models as m
from agent_platform.modules.manufacturing.service import ManufacturingService


def test_inspection_projection_uses_snapshot_and_never_foreign_workspace(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "projection.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    reader = Actor("quality-reader", "workshop-a", frozenset({"m11.read"}))
    with Session(engine) as session:
        product = m.Product(workspace_id="workshop-a", name="精密零件")
        device = m.Device(workspace_id="workshop-b", name="不可泄露设备名称")
        template = m.InspectionTemplate(workspace_id="workshop-a", name="已更名标准", target_type="product", metrics=[])
        session.add_all([product, device, template])
        session.flush()
        batch = m.ProductBatch(workspace_id="workshop-a", name="20261020-A", product_id=product.id, external_ref="source-row-78")
        session.add(batch)
        session.flush()
        # 人工构造历史脏引用，仅验证读取隔离；生产创建入口本来会拒绝跨工作区设备。
        row = m.Inspection(workspace_id="workshop-a", name="批次检验", target_type="product", product_id=product.id, device_id=device.id, batch_id=batch.id, template_id=template.id, template_revision=1, standard_snapshot={"name": "创建时的检验标准"})
        session.add(row)
        session.flush()
        service = ManufacturingService(session, reader)
        projected = service.list(m.Inspection)["items"][0]
        assert projected["device_name"] is None
        assert projected["product_name"] == "精密零件"
        assert projected["batch_number"] == "20261020-A" and projected["batch_external_ref"] == "source-row-78"
        assert projected["template_name"] == "创建时的检验标准"
        assert "不可泄露设备名称" not in str(projected)
        row.standard_snapshot = {}
        session.flush()
        assert service.inspection_views([row])[0]["template_name"] == "已更名标准"
        session.rollback()
    engine.dispose()


def test_http_m11_reader_gets_names_without_m10_or_data_permissions(tmp_path):
    settings = Settings(data_dir=tmp_path / "projection-http", enabled_modules=("identity", "manufacturing"))
    app = create_app(settings)
    with TestClient(app) as client:
        setup = client.post("/api/v1/auth/setup", json={"username": "projection-admin", "password": "Projection-password-2026", "display_name": "管理员", "workspace_name": "隔离名称测试"}).json()
        client.headers["X-CSRF-Token"] = setup["csrf_token"]
        device = client.post("/api/v1/devices", json={"name": "循环泵02"}).json()
        template = client.post("/api/v1/inspection-templates", json={"name": "泵温度标准V1", "target_type": "device", "metrics": [{"id": "temp", "name": "温度", "unit": "℃", "standard_max": "80"}]}).json()
        inspection = client.post("/api/v1/inspections", json={"name": "设备检验", "target_type": "device", "device_id": device["id"], "template_id": template["id"], "template_revision": 1}).json()
        me = client.get("/api/v1/me").json()
        actor = Actor("quality-reader", me["workspace"]["id"], frozenset({"m11.read"}))
        app.dependency_overrides[get_actor] = lambda: actor
        listed = client.get("/api/v1/inspections")
        detail = client.get(f'/api/v1/inspections/{inspection["id"]}')
        assert listed.status_code == detail.status_code == 200
        for projected in (listed.json()["items"][0], detail.json()):
            assert projected["device_name"] == "循环泵02" and projected["template_name"] == "泵温度标准V1"
            assert projected["product_name"] is None and projected["batch_number"] is None
        assert client.get("/api/v1/devices").status_code == 403
        assert client.post("/api/v1/inspections", json={"name": "禁止越权"}).status_code == 403
        app.dependency_overrides.clear()
