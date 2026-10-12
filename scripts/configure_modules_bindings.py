# -*- coding: utf-8 -*-
"""Configure workflows into Equipment (Alerts) and Quality Inspection modules.

1. Equipment Module:
   - 6 Core Equipment Devices in equipment_devices (domains=['alert'])
   - DeviceWorkflowBinding (domain='alert') linking to 6 published equipment workflows
   - Workflow node deviceId alignment

2. Quality Module:
   - 9 Quality Station Devices in equipment_devices (domains=['quality'])
   - DeviceWorkflowBinding (domain='quality') linking to 9 published quality workflows
   - Update configurations/quality_catalog.json for /api/v1/quality/products
   - Register QualityMetric and QualityTemplate records for traceability

Strictly constrained to local szh_new (127.0.0.1:3306).
"""
import copy
import json
import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.db import _SessionLocal
from app.ids import new_id
from app.modules.alerts.models import Device
from app.modules.workflows.models import Workflow, DeviceWorkflowBinding
from app.modules.quality.models import QualityMetric, QualityTemplate, QualityTemplateRevision

WORKSPACE_ID = "01M0EFJF2N7QK0B3G4JH01T1E1"
USER_ID = "01M0EFJF2NQ0T9VSS751J43EA8"
DATASOURCE_ID = "01M24QGNNN404ZF3A9XB48FRY5"  # local szh_new MySQL

EQUIPMENT_CONFIG = [
    {
        "code": "EQ-110KV-MAIN-TR",
        "name": "110kV变电站主变压器",
        "category": "供电变电",
        "location": "110kV变电站主变区",
        "model": "SZ11-50000/110",
        "manufacturer": "特变电工",
        "workflow_id": "01M2N83BRGJZTDYCTEG3DCQ580",  # 110kV站主变压器监控工作流
    },
    {
        "code": "EQ-CARBIDE-TR-01",
        "name": "电石炉供电变压器",
        "category": "炉变供电",
        "location": "电石炉变压器室",
        "model": "HKSSPZ-45000/35",
        "manufacturer": "正泰电气",
        "workflow_id": "01M2N83BYVQ7KH2D8XNWMWJ45D",  # 电石炉供电变压器监控工作流
    },
    {
        "code": "EQ-FURNACE-ROBOT-01",
        "name": "冶炼出炉机器人",
        "category": "出炉作业",
        "location": "1#电石炉出炉平台",
        "model": "RB-CR-500",
        "manufacturer": "宝钢智能",
        "workflow_id": "01M2N83C5Q0EVDGWDQF53FQ382",  # 冶炼出炉机器人监控工作流
    },
    {
        "code": "EQ-CIRCULAR-FEEDER-02",
        "name": "环形加料机",
        "category": "加料系统",
        "location": "电石炉炉顶环形轨道",
        "model": "CF-3600-D",
        "manufacturer": "重机通用",
        "workflow_id": "01M2N83CC74XXN6BX09F024V5G",  # 环形加料机运行监控工作流
    },
    {
        "code": "EQ-HYDRAULIC-STATION-01",
        "name": "电石炉液压站",
        "category": "动力液压",
        "location": "主厂房一层液压泵站间",
        "model": "HY-SYS-31.5",
        "manufacturer": "派克汉尼汾",
        "workflow_id": "01M2N83CJGRXG347725P4YKA3Y",  # 电石炉液压站监控工作流
    },
    {
        "code": "EQ-DCS-CENTRAL-01",
        "name": "集控室中枢及DCS机柜间",
        "category": "中控网络",
        "location": "生产集控大厅及DCS机房",
        "model": "ECS-700",
        "manufacturer": "中控技术",
        "workflow_id": "01M2N83CRS0X3JWQEX6J5EDXVF",  # 集控室中枢及DCS机柜巡检工作流
    },
]

QUALITY_STATIONS = [
    {
        "code": "QC-COKE-STATION",
        "name": "焦炭进厂质检工作站",
        "category": "原料质检",
        "location": "原料取样化验室",
        "workflow_id": "01M2N839WPPP2EKAH0TG99V4TC",  # 焦炭进厂质检工作流
    },
    {
        "code": "QC-LIMESTONE-STATION",
        "name": "石灰石进厂质检工作站",
        "category": "原料质检",
        "location": "石灰石料场取样站",
        "workflow_id": "01M2N83A4M6NV0RWT75N3CZTHG",  # 石灰石进厂质检工作流
    },
    {
        "code": "QC-SEMI-COKE-STATION",
        "name": "兰炭进厂质检工作站",
        "category": "原料质检",
        "location": "兰炭料仓化验工位",
        "workflow_id": "01M2N83AB3ZYTG150GFE40E8KZ",  # 兰炭进厂质检工作流
    },
    {
        "code": "QC-LIME-STATION",
        "name": "生石灰进厂质检工作站",
        "category": "原料质检",
        "location": "生石灰储仓化验站",
        "workflow_id": "01M2N83AHQBV8CG6BGN7JEF4D0",  # 生石灰进厂质检工作流
    },
    {
        "code": "QC-ELECTRODE-PASTE-STATION",
        "name": "电极糊进厂质检工作站",
        "category": "辅料质检",
        "location": "电极糊库检测试验室",
        "workflow_id": "01M2N83AR4XSCZYX6GKGXX3DT1",  # 电极糊进厂质检工作流
    },
    {
        "code": "QC-FURNACE-CARBIDE-STATION",
        "name": "炉前电石快速检验站",
        "category": "过程质检",
        "location": "1#电石炉炉前分析室",
        "workflow_id": "01M2N83AYF221MR3QT59V5TN7M",  # 炉前电石快速质检工作流
    },
    {
        "code": "QC-FINISHED-CARBIDE-STATION",
        "name": "出厂成品电石品质分级站",
        "category": "成品质检",
        "location": "成品电石发运站化验室",
        "workflow_id": "01M2N83B58T770B12YS09J5NC4",  # 成品电石品质分级质检工作流
    },
    {
        "code": "QC-FINISHED-LIME-STATION",
        "name": "成品石灰品质质检工作站",
        "category": "成品质检",
        "location": "石灰窑出料化验工位",
        "workflow_id": "01M2N83BC35Q3X69A3G9ZKTRVJ",  # 成品石灰质检工作流
    },
    {
        "code": "QC-SCREENED-COKE-STATION",
        "name": "筛分焦炭品质质检工作站",
        "category": "成品质检",
        "location": "焦炭筛分楼分析室",
        "workflow_id": "01M2N83BJ87FH8XC5T0GXAR439",  # 筛分焦炭质检工作流
    },
]


def upsert_device(db, *, code, name, category, location, domains, model="", manufacturer=""):
    dev = db.query(Device).filter(
        Device.workspace_id == WORKSPACE_ID,
        Device.code == code
    ).first()
    if dev is None:
        dev = Device(
            id=new_id(),
            workspace_id=WORKSPACE_ID,
            owner_user_id=USER_ID,
            code=code,
            name=name,
            category=category,
            model=model,
            manufacturer=manufacturer,
            location=location,
            protocol="HTTP",
            datasource_id=DATASOURCE_ID,
            domains=domains,
            description=f"{name}（系统自动配置）",
            status="active",
            version=1,
        )
        db.add(dev)
        db.flush()
        print(f"  [+] Created device: {name} ({code}) -> {dev.id}")
    else:
        dev.name = name
        dev.category = category
        dev.location = location
        dev.model = model or dev.model
        dev.manufacturer = manufacturer or dev.manufacturer
        dev.datasource_id = DATASOURCE_ID
        dev.domains = domains
        dev.status = "active"
        dev.updated_at = datetime.utcnow()
        dev.version += 1
        db.flush()
        print(f"  [*] Updated device: {name} ({code}) -> {dev.id}")
    return dev


def bind_workflow_to_device(db, *, device_id, domain, workflow_id):
    # Remove stale bindings for this device/domain or workflow
    db.query(DeviceWorkflowBinding).filter(
        DeviceWorkflowBinding.workspace_id == WORKSPACE_ID,
        DeviceWorkflowBinding.device_id == device_id,
        DeviceWorkflowBinding.domain == domain,
    ).delete(synchronize_session=False)

    db.query(DeviceWorkflowBinding).filter(
        DeviceWorkflowBinding.workflow_id == workflow_id,
    ).delete(synchronize_session=False)
    db.flush()

    binding = DeviceWorkflowBinding(
        id=new_id(),
        workspace_id=WORKSPACE_ID,
        owner_user_id=USER_ID,
        device_id=device_id,
        domain=domain,
        workflow_id=workflow_id,
        provider="platform",
    )
    db.add(binding)
    db.flush()
    print(f"  [+] Bound {domain} device {device_id} -> WF {workflow_id}")

    # Align workflow node deviceId
    wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if wf and wf.graph:
        graph = copy.deepcopy(wf.graph)
        kinds = {"alert-evaluate"} if domain == "alert" else {"qc-appearance", "qc-component", "qc-evaluate"}
        changed = False
        for node in graph.get("nodes", []):
            if node.get("kind") in kinds:
                cfg = node.setdefault("config", {})
                if cfg.get("deviceId") != device_id:
                    cfg["deviceId"] = device_id
                    changed = True
        if changed:
            wf.graph = graph
            wf.version += 1
            db.flush()
            print(f"  [*] Aligned workflow graph node deviceId for {wf.name}")


def clean_stale_bindings(db):
    """Remove bindings pointing to non-existent or deleted workflows."""
    stale = 0
    for b in db.query(DeviceWorkflowBinding).all():
        wf = db.query(Workflow).filter(Workflow.id == b.workflow_id).first()
        if wf is None or wf.status == "deleted":
            db.delete(b)
            stale += 1
    if stale > 0:
        db.flush()
        print(f"Cleaned up {stale} stale bindings pointing to deleted workflows.")


def update_quality_catalog():
    """Device bindings must not overwrite the reviewed product catalogue."""
    print("Quality catalogue preserved; use scripts/rebuild_quality_catalog.py after reviewing local source evidence.")


def populate_quality_metrics_and_templates(db):
    """Upsert standard quality metrics and templates in quality_metrics & quality_templates."""
    metrics_def = [
        {"name": "水分 (Mt)", "category": "numeric", "unit": "%", "min": "0", "max": "12.0", "desc": "焦炭/兰炭表面及全水分"},
        {"name": "灰分 (Ad)", "category": "numeric", "unit": "%", "min": "0", "max": "15.0", "desc": "焦炭/兰炭干燥基灰分"},
        {"name": "固定碳 (FCd)", "category": "numeric", "unit": "%", "min": "82.0", "max": "100.0", "desc": "焦炭/兰炭干燥基固定碳"},
        {"name": "挥发分 (Vdaf)", "category": "numeric", "unit": "%", "min": "0.5", "max": "3.5", "desc": "焦炭/兰炭干燥无灰基挥发分"},
        {"name": "全硫 (St,d)", "category": "numeric", "unit": "%", "min": "0", "max": "0.5", "desc": "焦炭全硫含量"},
        {"name": "有效氧化钙 (CaO)", "category": "numeric", "unit": "%", "min": "85.0", "max": "100.0", "desc": "生石灰有效氧化钙含量"},
        {"name": "活性度", "category": "numeric", "unit": "mL", "min": "300", "max": "450", "desc": "生石灰4mol/L盐酸滴定活性度"},
        {"name": "生烧率", "category": "numeric", "unit": "%", "min": "0", "max": "8.0", "desc": "石灰生烧率"},
        {"name": "过烧率", "category": "numeric", "unit": "%", "min": "0", "max": "5.0", "desc": "石灰过烧率"},
        {"name": "氧化钙 (CaO) - 石灰石", "category": "numeric", "unit": "%", "min": "52.0", "max": "100.0", "desc": "石灰石原料碳酸钙折算CaO"},
        {"name": "二氧化硅 (SiO2)", "category": "numeric", "unit": "%", "min": "0", "max": "2.0", "desc": "石灰石/石灰杂质二氧化硅"},
        {"name": "电极糊灰分", "category": "numeric", "unit": "%", "min": "0", "max": "4.5", "desc": "电极糊煅烧后灰分"},
        {"name": "电极糊延伸率", "category": "numeric", "unit": "%", "min": "20.0", "max": "45.0", "desc": "电极糊热态延伸流动性"},
        {"name": "电石快速发气量 (炉前)", "category": "numeric", "unit": "L/kg", "min": "260", "max": "340", "desc": "电石炉炉前出炉快速检测发气量"},
        {"name": "成品电石发气量 (出厂)", "category": "numeric", "unit": "L/kg", "min": "280", "max": "330", "desc": "成品电石标准状态20℃, 101.3kPa发气量"},
        {"name": "电石粉化率", "category": "numeric", "unit": "%", "min": "0", "max": "8.0", "desc": "成品电石粒度筛分碎粉比例"},
        {"name": "筛分焦炭粒度 (>25mm)", "category": "numeric", "unit": "%", "min": "85.0", "max": "100.0", "desc": "电石冶炼主体大颗粒焦炭占比"},
    ]
    metric_ids = {}
    for m in metrics_def:
        row = db.query(QualityMetric).filter(
            QualityMetric.workspace_id == WORKSPACE_ID,
            QualityMetric.name == m["name"]
        ).first()
        if not row:
            row = QualityMetric(
                id=new_id(),
                workspace_id=WORKSPACE_ID,
                owner_user_id=USER_ID,
                name=m["name"],
                category=m["category"],
                unit=m["unit"],
                standard_min=m["min"],
                standard_max=m["max"],
                description=m["desc"],
                status="active",
                version=1,
            )
            db.add(row)
            db.flush()
        else:
            row.status = "active"
            row.standard_min = m["min"]
            row.standard_max = m["max"]
            db.flush()
        metric_ids[m["name"]] = row.id

    templates_def = [
        {"name": "焦炭进厂质量检验标准模板", "metrics": ["水分 (Mt)", "灰分 (Ad)", "固定碳 (FCd)", "挥发分 (Vdaf)", "全硫 (St,d)"]},
        {"name": "生石灰质量检验标准模板", "metrics": ["有效氧化钙 (CaO)", "活性度", "生烧率", "过烧率", "二氧化硅 (SiO2)"]},
        {"name": "石灰石进厂质量检验标准模板", "metrics": ["氧化钙 (CaO) - 石灰石", "二氧化硅 (SiO2)"]},
        {"name": "电极糊进厂质量检验标准模板", "metrics": ["电极糊灰分", "电极糊延伸率"]},
        {"name": "炉前电石快速检验标准模板", "metrics": ["电石快速发气量 (炉前)"]},
        {"name": "成品电石品质分级检验标准模板", "metrics": ["成品电石发气量 (出厂)", "电石粉化率"]},
        {"name": "筛分焦炭分级检验标准模板", "metrics": ["水分 (Mt)", "灰分 (Ad)", "筛分焦炭粒度 (>25mm)"]},
    ]
    for t in templates_def:
        t_row = db.query(QualityTemplate).filter(
            QualityTemplate.workspace_id == WORKSPACE_ID,
            QualityTemplate.name == t["name"]
        ).first()
        m_ids = [metric_ids[name] for name in t["metrics"] if name in metric_ids]
        if not t_row:
            t_row = QualityTemplate(
                id=new_id(),
                workspace_id=WORKSPACE_ID,
                owner_user_id=USER_ID,
                name=t["name"],
                description=f"{t['name']}（涵盖关键受控指标）",
                metric_ids=m_ids,
                status="active",
                version=1,
            )
            db.add(t_row)
            db.flush()
        else:
            t_row.status = "active"
            t_row.metric_ids = m_ids
            db.flush()

        # Build valid revision snapshot
        from app.modules.quality.registry import view
        metrics_objs = [view("metrics", db.query(QualityMetric).filter(QualityMetric.id == mid).first()) for mid in m_ids]
        snapshot = {
            **view("templates", t_row),
            "metricIds": m_ids,
            "metrics": metrics_objs
        }

        rev = db.query(QualityTemplateRevision).filter(
            QualityTemplateRevision.workspace_id == WORKSPACE_ID,
            QualityTemplateRevision.template_id == t_row.id,
            QualityTemplateRevision.revision == t_row.version
        ).first()
        if not rev:
            rev = QualityTemplateRevision(
                id=new_id(),
                workspace_id=WORKSPACE_ID,
                owner_user_id=USER_ID,
                template_id=t_row.id,
                revision=t_row.version,
                snapshot=snapshot
            )
            db.add(rev)
        else:
            rev.snapshot = snapshot
        db.flush()
    print("Populated standard quality metrics and templates with valid snapshots.")


def main():
    db = _SessionLocal()
    try:
        print("=== Step 1: Clean stale bindings ===")
        clean_stale_bindings(db)

        print("\n=== Step 2: Configure Equipment Module (domain='alert') ===")
        for eq in EQUIPMENT_CONFIG:
            dev = upsert_device(
                db,
                code=eq["code"],
                name=eq["name"],
                category=eq["category"],
                location=eq["location"],
                domains=["alert"],
                model=eq.get("model", ""),
                manufacturer=eq.get("manufacturer", ""),
            )
            bind_workflow_to_device(
                db,
                device_id=dev.id,
                domain="alert",
                workflow_id=eq["workflow_id"]
            )

        print("\n=== Step 3: Configure Quality Module (domain='quality') ===")
        for qc in QUALITY_STATIONS:
            dev = upsert_device(
                db,
                code=qc["code"],
                name=qc["name"],
                category=qc["category"],
                location=qc["location"],
                domains=["quality"],
            )
            bind_workflow_to_device(
                db,
                device_id=dev.id,
                domain="quality",
                workflow_id=qc["workflow_id"]
            )

        print("\n=== Step 4: Populate Quality Metrics and Templates ===")
        populate_quality_metrics_and_templates(db)

        db.commit()
        print("\nDatabase changes successfully committed!")

        print("\n=== Step 5: Update quality_catalog.json ===")
        update_quality_catalog()

        print("\nAll configuration completed successfully!")
    except Exception as e:
        db.rollback()
        print(f"Error occurred: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
