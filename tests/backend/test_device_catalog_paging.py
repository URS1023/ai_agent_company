"""Bounded device loading with exact source-authorized counts and pages.

中文说明：设备目录只物化请求的那一页、按源批量取数，计数必须是源授权范围内的真实值；
域/关键词/已删除过滤精确；权限与授权过滤发生在 count 与 offset 之前。
隔离方式：client 夹具（内存 SQLite）+ 统计实际查询负载，不连业务库。
"""
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import event, select

from app.db import get_engine
from app.ids import new_id
from app.models import AuthIdentity, DataSource
from app.modules.alerts.models import Device, DeviceQualitySource
from app.modules.system.models import ResourceGrant, Role
from test_system_foundation import activate_user, create_role, create_user

API = "/api/v1/alerts/device-catalogue"


@contextmanager
def measure_loads():
    measured = {"devices": 0, "selects": 0}
    def loaded(row, context):
        measured["devices"] += 1
    def executed(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            measured["selects"] += 1
    engine = get_engine()
    event.listen(Device, "load", loaded)
    event.listen(engine, "before_cursor_execute", executed)
    try:
        yield measured
    finally:
        event.remove(Device, "load", loaded)
        event.remove(engine, "before_cursor_execute", executed)


def read_page(client, headers, **params):
    with measure_loads() as measured:
        response = client.get(API, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"], measured


def seed_devices(db, headers, *, count=45):
    owner = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "workspace-admin")).user_id
    workspace = headers["X-Workspace-Id"]
    now = datetime.utcnow()
    rows = [Device(workspace_id=workspace, owner_user_id=owner, code=f"PAGE-{index:03}",
                   name=f"设备{index}", domains=["alert", "quality"] if index % 2 == 0 else ["alert"],
                   updated_at=now - timedelta(seconds=index)) for index in range(count)]
    db.add_all(rows)
    db.commit()
    return rows


def test_catalogue_materializes_only_the_requested_page_and_batches_sources(client, administrator, db):
    devices = seed_devices(db, administrator)
    one, one_loads = read_page(client, administrator, limit=1)
    page, loads = read_page(client, administrator, limit=12, offset=24)
    end, end_loads = read_page(client, administrator, limit=12, offset=45)
    print({"one": one_loads, "page12offset24": loads, "empty": end_loads})
    assert one["total"] == page["total"] == end["total"] == 45
    assert [row["id"] for row in page["items"]] == [row.id for row in devices[24:36]]
    assert end["items"] == []
    assert loads["devices"] <= 12, "A page materializes devices outside its requested range"
    assert end_loads["devices"] == 0, "Empty pages still load the entire device catalogue"
    assert loads["selects"] <= one_loads["selects"] + 2, "Each card issues a separate source-mapping query"


def test_catalogue_exact_domain_keyword_and_deleted_filters(client, administrator, db):
    devices = seed_devices(db, administrator, count=9)
    devices[0].status = "deleted"
    devices[2].name = "literal_%_device"
    devices[4].name = "literalAB_device"
    # Editing names/status invokes updated_at's onupdate default. Preserve the
    # fixture's explicit order so this filter test does not depend on clock ties.
    now = datetime.utcnow()
    for index, device in enumerate(devices):
        device.updated_at = now - timedelta(seconds=index)
    db.commit()
    quality, _ = read_page(client, administrator, domain="quality", limit=2, offset=1)
    assert quality["total"] == 4
    assert [row["id"] for row in quality["items"]] == [devices[4].id, devices[6].id]
    literal, _ = read_page(client, administrator, keyword="_%_", domain="quality")
    assert literal["total"] == 1 and literal["items"][0]["id"] == devices[2].id
    missing, _ = read_page(client, administrator, keyword="no-such-device")
    assert missing["total"] == 0 and missing["items"] == []


def test_source_scope_and_grants_filter_before_count_and_offset(client, administrator, db):
    devices = seed_devices(db, administrator, count=6)
    broad = create_role(client, administrator, "device_catalog_reader", permissions=["home.view", "devices.view"], scope="workspace")
    sources = create_role(client, administrator, "own_sources", permissions=["datasources.view"], scope="self")
    create_user(client, administrator, [broad["id"], sources["id"]])
    reader = activate_user(client)
    owner = db.scalar(select(AuthIdentity).where(AuthIdentity.username_normalized == "test-reader")).user_id
    now = datetime.utcnow()
    allowed = DataSource(id=new_id(), workspace_id=devices[0].workspace_id, owner_user_id=owner,
                         name="允许来源", provider="http", connection_config={}, created_at=now, updated_at=now)
    denied = DataSource(id=new_id(), workspace_id=devices[0].workspace_id, owner_user_id=devices[0].owner_user_id,
                        name="未授权来源", provider="http", connection_config={}, created_at=now, updated_at=now)
    db.add_all([allowed, denied])
    db.flush()
    devices[0].datasource_id = denied.id
    devices[1].datasource_id = allowed.id
    # Device 2 has no primary source but a hidden additional detector. Device 3
    # has both allowed and denied detectors: every linked source must be visible.
    for index, source in [(2, denied), (3, allowed), (3, denied), (4, allowed)]:
        db.add(DeviceQualitySource(workspace_id=devices[index].workspace_id,
                                  owner_user_id=devices[index].owner_user_id,
                                  device_id=devices[index].id, datasource_id=source.id))
    # Assigning primary sources updates timestamps; make the intended order
    # explicit after the fixture edits rather than assuming insertion order.
    for index, device in enumerate(devices):
        device.updated_at = now - timedelta(seconds=index)
    db.commit()
    page, _ = read_page(client, reader, limit=2, offset=1)
    assert page["total"] == 3
    assert [row["id"] for row in page["items"]] == [devices[4].id, devices[5].id]
    assert page["items"][0]["qualityDatasourceIds"] == [allowed.id]
    grant = ResourceGrant(workspace_id=devices[0].workspace_id, resource_type="datasources", resource_id=denied.id,
                          subject_type="user", subject_id=owner, actions=["view"])
    db.add(grant)
    db.commit()
    shared, _ = read_page(client, reader, limit=2, offset=1)
    assert shared["total"] == 6
    assert [row["id"] for row in shared["items"]] == [devices[1].id, devices[2].id]
    db.delete(grant)
    db.get(Role, sources["id"]).permissions = []
    db.commit()
    revoked, _ = read_page(client, reader)
    assert revoked["total"] == 1 and [row["id"] for row in revoked["items"]] == [devices[5].id]
