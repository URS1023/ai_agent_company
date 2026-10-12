"""Local SQLite catalogue and optional photo lifecycle; no source execution.

中文说明：业务目录（设备卡）与可选照片生命周期：分页/域筛选/草稿状态、照片上传替换删除、
非法照片不落盘、照片需要设备编辑权限与授权文件查看权限、产品照片走独立绑定。
隔离方式：monkeypatch + tmp_path + 内存 SQLite，不执行任何业务数据源查询。
边界：非法图片不得留下文件；旧库升级需新增迁移而不是重建表。
"""
from io import BytesIO
from datetime import datetime

import pytest
from PIL import Image
from sqlalchemy import inspect, select

from app.config import settings
from app.models import File
from app.modules.alerts.models import BusinessCardImage
from app.modules.quality import product_catalog
from test_device_workflow_entry import create_device, open_entry
from test_system_foundation import create_role, create_user, activate_user

API = "/api/v1/alerts"


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "files"))


def picture(color="navy"):
    output = BytesIO()
    Image.new("RGB", (800, 400), color).save(output, "PNG")
    return output.getvalue()


def upload(client, headers, target, *, version=0, data=None):
    return client.post(API + "/card-images/" + target, headers=headers, params={"version": version},
                       files={"file": ("image.png", picture() if data is None else data, "image/png")})


def test_device_paging_domain_count_search_and_draft_status(client, administrator):
    devices = [create_device(client, administrator, code=f"DEV-{index}", name=f"设备{index}", domains=["alert"]) for index in range(13)]
    create_device(client, administrator, code="QUALITY", domains=["quality"])
    before = client.get(API + "/device-catalogue", headers=administrator).json()["data"]
    assert before["total"] == 13 and len(before["items"]) == 12
    assert all(row["image"] is None and row["workflow"] is None for row in before["items"])
    last = client.get(API + "/device-catalogue", headers=administrator, params={"offset": 12}).json()["data"]
    assert len(last["items"]) == 1
    assert not {r["id"] for r in before["items"]} & {r["id"] for r in last["items"]}
    open_entry(client, administrator, devices[0]["id"], "alert")
    found = client.get(API + "/device-catalogue", headers=administrator, params={"keyword": "DEV-0"}).json()["data"]
    assert found["total"] == 1 and found["items"][0]["workflow"]["status"] == "draft"
    quality = client.get(API + "/device-catalogue", headers=administrator, params={"domain": "quality"}).json()["data"]
    assert quality["total"] == 1 and quality["items"][0]["code"] == "QUALITY"


def test_optional_photo_upload_replace_remove_and_reload(client, administrator, db):
    device = create_device(client, administrator)
    target = "device/" + device["id"]
    first = upload(client, administrator, target)
    assert first.status_code == 200, first.text
    image = first.json()["data"]
    listed = client.get(API + "/device-catalogue", headers=administrator).json()["data"]["items"][0]
    assert listed["image"] == image and listed["version"] == device["version"]
    content = client.get(API + "/card-images/" + target + "/content", headers=administrator)
    assert content.status_code == 200 and content.headers["cache-control"] == "no-store"
    with Image.open(BytesIO(content.content)) as thumbnail:
        assert thumbnail.format == "WEBP" and thumbnail.size == (640, 320)
    assert upload(client, administrator, target).status_code == 409
    replacement = upload(client, administrator, target, version=image["version"], data=picture("red")).json()["data"]
    assert replacement["fileId"] != image["fileId"] and replacement["version"] == image["version"] + 1
    removed = client.delete(API + "/card-images/" + target, headers=administrator, params={"version": replacement["version"]})
    assert removed.status_code == 200 and removed.json()["data"]["fileId"] is None
    assert client.get(API + "/card-images/" + target + "/content", headers=administrator).status_code == 404
    assert upload(client, administrator, target, version=removed.json()["data"]["version"]).status_code == 200
    assert len(db.scalars(select(BusinessCardImage)).all()) == 1


@pytest.mark.parametrize("data", [b"<svg>unsafe</svg>", b"broken", b"x" * (5 * 1024 * 1024 + 1), b""], ids=["svg", "broken", "over-limit", "empty"])
def test_invalid_photo_does_not_save_a_file(client, administrator, db, data):
    device = create_device(client, administrator)
    response = upload(client, administrator, "device/" + device["id"], data=data)
    assert response.status_code == 422
    assert not db.scalars(select(File).where(File.source_type == "business_card_image")).all()


def test_photo_requires_device_edit_and_authorized_file_view(client, administrator):
    device = create_device(client, administrator)
    target = "device/" + device["id"]
    assert upload(client, administrator, target).status_code == 200
    role = create_role(client, administrator, permissions=["home.view", "devices.view"], scope="workspace")
    create_user(client, administrator, [role["id"]])
    reader = activate_user(client)
    assert upload(client, reader, target).status_code in {403, 404}
    assert client.get(API + "/card-images/" + target + "/content", headers=reader).status_code in {403, 404}
    assert client.get(API + "/card-images/" + target + "/content").status_code == 401
    assert upload(client, administrator, "device/not-a-device").status_code == 404


def test_product_photo_has_separate_binding_and_respects_catalogue_access(client, administrator, monkeypatch):
    monkeypatch.setattr(product_catalog, "catalogue", lambda ctx: {"entries": [{"productId": "product-one"}]})
    first = upload(client, administrator, "product/product-one")
    assert first.status_code == 200, first.text
    assert client.get(API + "/card-images/product/product-one/content", headers=administrator).status_code == 200
    assert upload(client, administrator, "product/product-other").status_code == 404
    monkeypatch.setattr(product_catalog, "catalogue", lambda ctx: {"entries": []})
    assert client.get(API + "/card-images/product/product-one/content", headers=administrator).status_code == 404


def test_existing_installation_adds_photo_table_with_new_migration(db):
    from app.schema import SCHEMA_VERSION, SchemaMigration, initialize_schema

    engine = db.get_bind()
    assert engine.url.get_backend_name() == "sqlite" and engine.url.database == ":memory:"
    assert SCHEMA_VERSION != "20260908_27"
    db.query(SchemaMigration).delete()
    db.add(SchemaMigration(version="20260908_27", checksum="pre-card-model", applied_at=datetime.utcnow()))
    db.commit()
    BusinessCardImage.__table__.drop(engine)
    initialize_schema(engine)
    assert "business_card_images" in inspect(engine).get_table_names()
    assert db.get(SchemaMigration, "20260908_27").checksum == "pre-card-model"
    assert db.get(SchemaMigration, SCHEMA_VERSION) is not None
