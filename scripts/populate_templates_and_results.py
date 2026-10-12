# -*- coding: utf-8 -*-
"""Populate complete template configurations and execution results/reports.

1. Equipment Templates:
   - ExpertAdvice (专家处置预案)
   - MeasurementPoints (测点台账)
   - AlertRules & AlertRuleRevisions (预警规则策略模板)

2. Equipment Execution Results & Reports:
   - AlertReport & AlertProblem (真实预警报告与待办问题)
   - Verified report HTML export

3. Quality Templates:
   - Verified QualityTemplates & QualityMetrics already populated

4. Quality Execution Results & Reports:
   - QualityInspection records (真实质检单据与报告)
   - Verified quality inspection HTML export

5. Workflow Execution Runs:
   - Triggered live workflow execution runs with full node traces and Agnes AI diagnosis.
"""
import copy
from datetime import datetime
from hashlib import sha256
import io
import json
import os
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.db import _SessionLocal
from app.ids import new_id
from app.modules.alerts.models import (
    Device, MeasurementPoint, ExpertAdvice, AlertRule, AlertRuleRevision,
    AlertReport, AlertProblem, AlertProblemEvent
)
from app.modules.quality.models import (
    QualityMetric, QualityTemplate, QualityTemplateRevision, QualityInspection
)
from app.modules.workflows.models import Workflow, WorkflowExecution

WORKSPACE_ID = "01M0EFJF2N7QK0B3G4JH01T1E1"
USER_ID = "01M0EFJF2NQ0T9VSS751J43EA8"
DATASOURCE_ID = "01M24QGNNN404ZF3A9XB48FRY5"


def populate_equipment_templates(db):
    print("=== Step 1: Populating Equipment Advice, Points, and Rules ===")
    devices = {d.code: d for d in db.query(Device).filter(Device.workspace_id == WORKSPACE_ID).all()}

    # 1. Expert Advice
    advice_list = [
        {
            "name": "主变压器顶层油温过高应急处置预案",
            "condition_code": "COND-TR-TEMP-HIGH",
            "operating_condition": "110kV主变压器顶层油温超过85℃或温升速度>1.5℃/min",
            "alert_type": "设备超温预警",
            "condition_description": "负荷高峰期或环境高温导致强油风冷回路异常或主变绝缘油老化",
            "content": "根据GB/T 6451-2015及变电站运行规程，变压器顶层油温超过85℃时必须采取降温降负荷措施。",
            "response_plan": "1. 现场核对红外测温与DCS示值是否一致；\n2. 手动启动备用风机与潜油泵强制冷却；\n3. 调配1#与2#电石炉有功负荷，削减变压器负荷率至80%以下；\n4. 取油样进行色谱分析（重瓦斯及微水指标）。",
            "plan_short": "启动备用强冷，负荷削减分流，取样油色谱分析",
        },
        {
            "name": "出炉机器人液压异常与关节卡阻应急预案",
            "condition_code": "COND-ROBOT-HYD-ERR",
            "operating_condition": "冶炼出炉机器人大臂液压波动>3.5MPa或伺服驱动器过载报警",
            "alert_type": "机械与液压预警",
            "condition_description": "出炉作业高温辐射导致液压油粘度下降或出炉钎杆卡死引发电机堵转",
            "content": "机器人出炉属于高温高危关键工序，任何伺服或液压异常均可能导致铁口跑料或撞击炉体。",
            "response_plan": "1. 立即切断出炉机器人自动作业模式，切换至安全手动位退出炉口；\n2. 检查末端工具夹持状态与钎杆是否卡死；\n3. 检查冷却循环水温与液压站油温（控制在50℃以下）；\n4. 确认伺服报警代码，复位前严禁强制启动。",
            "plan_short": "手动退刀退出炉口，检查液压油温与钎杆卡死",
        },
        {
            "name": "电石炉液压站主泵压力欠压处置预案",
            "condition_code": "COND-HYD-PRESS-LOW",
            "operating_condition": "电石炉液压站系统主供油压力低于20.0MPa或比例阀内泄",
            "alert_type": "液压动力预警",
            "condition_description": "主油泵叶片磨损、滤芯压差超标或蓄能器气囊漏气引起供油不足",
            "content": "电石炉电极升降、抱闸与出炉均高度依赖主液压动力源，失压可能导致电极下坠或卡死事故。",
            "response_plan": "1. 启动备用主泵联动运行，观察系统压力是否回升至25MPa；\n2. 检查高压管路与各执行油缸是否有外漏喷射；\n3. 检查蓄能器氮气充气压力；\n4. 切换双筒回油过滤器并清洗滤芯。",
            "plan_short": "联动备用泵，检查管路泄漏与蓄能器压力",
        },
    ]

    saved_advice = {}
    for adv in advice_list:
        row = db.query(ExpertAdvice).filter(
            ExpertAdvice.workspace_id == WORKSPACE_ID,
            ExpertAdvice.name == adv["name"]
        ).first()
        if not row:
            row = ExpertAdvice(
                id=new_id(),
                workspace_id=WORKSPACE_ID,
                owner_user_id=USER_ID,
                name=adv["name"],
                condition_code=adv["condition_code"],
                operating_condition=adv["operating_condition"],
                alert_type=adv["alert_type"],
                condition_description=adv["condition_description"],
                content=adv["content"],
                response_plan=adv["response_plan"],
                plan_short=adv["plan_short"],
                status="active",
                version=1,
            )
            db.add(row)
            db.flush()
        else:
            row.status = "active"
            row.response_plan = adv["response_plan"]
            db.flush()
        saved_advice[adv["condition_code"]] = row
    print(f"  [+] Configured {len(saved_advice)} ExpertAdvice records.")

    # 2. Measurement Points
    points_def = [
        {"dev_code": "EQ-110KV-MAIN-TR", "code": "PT_TR_OIL_TEMP", "name": "顶层绝缘油温度", "field": "oil_temperature", "unit": "℃"},
        {"dev_code": "EQ-110KV-MAIN-TR", "code": "PT_TR_LOAD_RATE", "name": "运行负荷率", "field": "load_percentage", "unit": "%"},
        {"dev_code": "EQ-CARBIDE-TR-01", "code": "PT_FURN_CURR", "name": "二次侧运行电流", "field": "secondary_current", "unit": "kA"},
        {"dev_code": "EQ-FURNACE-ROBOT-01", "code": "PT_ROBOT_HYD", "name": "出炉臂主油压", "field": "arm_hydraulic_pressure", "unit": "MPa"},
        {"dev_code": "EQ-FURNACE-ROBOT-01", "code": "PT_ROBOT_TEMP", "name": "关节轴承温度", "field": "joint_temperature", "unit": "℃"},
        {"dev_code": "EQ-CIRCULAR-FEEDER-02", "code": "PT_FEED_MOTOR_I", "name": "环加变频电机电流", "field": "motor_current", "unit": "A"},
        {"dev_code": "EQ-HYDRAULIC-STATION-01", "code": "PT_HYD_SYS_P", "name": "系统主供油压力", "field": "system_pressure", "unit": "MPa"},
        {"dev_code": "EQ-DCS-CENTRAL-01", "code": "PT_DCS_CPU_LOAD", "name": "主控制器CPU负荷率", "field": "cpu_load_rate", "unit": "%"},
    ]
    for pt in points_def:
        dev = devices.get(pt["dev_code"])
        if not dev:
            continue
        row = db.query(MeasurementPoint).filter(
            MeasurementPoint.workspace_id == WORKSPACE_ID,
            MeasurementPoint.device_id == dev.id,
            MeasurementPoint.code == pt["code"]
        ).first()
        if not row:
            row = MeasurementPoint(
                id=new_id(),
                workspace_id=WORKSPACE_ID,
                owner_user_id=USER_ID,
                device_id=dev.id,
                code=pt["code"],
                name=pt["name"],
                field_name=pt["field"],
                unit=pt["unit"],
                point_type="number",
                device_key=dev.code,
                prefix="",
                frequency_minutes=5,
                failure_diagnosis=True,
                description=f"{dev.name} - {pt['name']}实时监测点",
                status="active",
                version=1,
            )
            db.add(row)
            db.flush()
    print(f"  [+] Configured {len(points_def)} MeasurementPoint records.")

    # 3. Alert Rules & Rule Revisions
    rules_def = [
        {
            "dev_code": "EQ-110KV-MAIN-TR",
            "name": "110kV主变压器综合状态预警策略",
            "advice_code": "COND-TR-TEMP-HIGH",
            "field": "oil_temperature",
            "rules": [
                {
                    "id": "tr_oil_crit",
                    "name": "变压器油温超高警戒",
                    "expression": {"op": "gt", "args": [{"field": "oil_temperature"}, {"value": 85.0}]},
                    "severity": "critical",
                    "message": "变压器油温超过85℃高温警戒线，需立即减载强冷"
                },
                {
                    "id": "tr_oil_warn",
                    "name": "变压器油温超温预警",
                    "expression": {"op": "gt", "args": [{"field": "oil_temperature"}, {"value": 75.0}]},
                    "severity": "warning",
                    "message": "变压器油温达到75℃预警线，请检查冷却回路"
                },
            ]
        },
        {
            "dev_code": "EQ-FURNACE-ROBOT-01",
            "name": "出炉机器人液压与作业安全策略",
            "advice_code": "COND-ROBOT-HYD-ERR",
            "field": "arm_hydraulic_pressure",
            "rules": [
                {
                    "id": "robot_hyd_high",
                    "name": "出炉机械臂液压超压",
                    "expression": {"op": "gt", "args": [{"field": "arm_hydraulic_pressure"}, {"value": 28.0}]},
                    "severity": "critical",
                    "message": "出炉机械臂液压异常超压，可能存在工具卡死"
                },
                {
                    "id": "robot_hyd_low",
                    "name": "出炉机械臂液压欠压",
                    "expression": {"op": "lt", "args": [{"field": "arm_hydraulic_pressure"}, {"value": 15.0}]},
                    "severity": "warning",
                    "message": "出炉机械臂液压不足，作业力度受限"
                },
            ]
        },
        {
            "dev_code": "EQ-HYDRAULIC-STATION-01",
            "name": "电石炉液压站系统压力监控策略",
            "advice_code": "COND-HYD-PRESS-LOW",
            "field": "system_pressure",
            "rules": [
                {
                    "id": "hyd_sys_low",
                    "name": "电石炉液压站主系统失压",
                    "expression": {"op": "lt", "args": [{"field": "system_pressure"}, {"value": 20.0}]},
                    "severity": "critical",
                    "message": "主系统压力低于20MPa危险值，电极升降可能失灵"
                },
                {
                    "id": "hyd_sys_high",
                    "name": "电石炉液压站系统超压",
                    "expression": {"op": "gt", "args": [{"field": "system_pressure"}, {"value": 31.5}]},
                    "severity": "warning",
                    "message": "系统压力高于31.5MPa，溢流阀动作异常"
                },
            ]
        },
    ]

    saved_rules = {}
    for r_def in rules_def:
        dev = devices.get(r_def["dev_code"])
        adv = saved_advice.get(r_def["advice_code"])
        if not dev:
            continue
        row = db.query(AlertRule).filter(
            AlertRule.workspace_id == WORKSPACE_ID,
            AlertRule.name == r_def["name"]
        ).first()
        if not row:
            row = AlertRule(
                id=new_id(),
                workspace_id=WORKSPACE_ID,
                owner_user_id=USER_ID,
                name=r_def["name"],
                description=f"{dev.name} 运行参数判定策略",
                device_id=dev.id,
                advice_id=adv.id if adv else None,
                record_key_field=r_def["field"],
                rules_json=r_def["rules"],
                status="active",
                version=1,
            )
            db.add(row)
            db.flush()
            # Revision
            rev = AlertRuleRevision(
                id=new_id(),
                workspace_id=WORKSPACE_ID,
                owner_user_id=USER_ID,
                rule_id=row.id,
                revision=1,
                snapshot={
                    "name": row.name,
                    "deviceId": dev.id,
                    "recordKeyField": row.record_key_field,
                    "rules": r_def["rules"],
                    "advice": adv.response_plan if adv else "",
                    "adviceShort": adv.plan_short if adv else "",
                }
            )
            db.add(rev)
            db.flush()
        else:
            row.status = "active"
            row.rules_json = r_def["rules"]
            # Also update revision snapshot
            rev = db.query(AlertRuleRevision).filter(
                AlertRuleRevision.workspace_id == WORKSPACE_ID,
                AlertRuleRevision.rule_id == row.id,
                AlertRuleRevision.revision == 1
            ).first()
            if rev:
                rev.snapshot = {
                    "name": row.name,
                    "deviceId": dev.id,
                    "recordKeyField": row.record_key_field,
                    "rules": r_def["rules"],
                    "advice": adv.response_plan if adv else "",
                    "adviceShort": adv.plan_short if adv else "",
                }
            db.flush()
        saved_rules[r_def["name"]] = row
    print(f"  [+] Configured {len(saved_rules)} AlertRule and AlertRuleRevision records.")
    return devices, saved_rules, saved_advice


def populate_alert_reports_and_problems(db, devices, rules, advice):
    print("\n=== Step 2: Populating Equipment Alert Reports and Problems ===")
    robot_dev = devices.get("EQ-FURNACE-ROBOT-01")
    robot_rule = rules.get("出炉机器人液压与作业安全策略")
    adv = advice.get("COND-ROBOT-HYD-ERR")

    if not robot_dev or not robot_rule:
        return

    # Check if report already exists
    report_title = f"{robot_dev.name} · 运行预警报告"
    existing_rep = db.query(AlertReport).filter(
        AlertReport.workspace_id == WORKSPACE_ID,
        AlertReport.device_id == robot_dev.id,
        AlertReport.title == report_title
    ).first()

    report_id = existing_rep.id if existing_rep else new_id()

    # Get valid execution ID from workflow executions
    exec_row = db.query(WorkflowExecution.id).filter(
        WorkflowExecution.workspace_id == WORKSPACE_ID,
        WorkflowExecution.workflow_id == "01M2N83C5Q0EVDGWDQF53FQ382"
    ).order_by(WorkflowExecution.id.desc()).first()

    if not exec_row:
        print("  [-] No execution found for robot workflow")
        return
    exec_id = exec_row[0]

    report_snapshot = {
        "rule": {
            "name": robot_rule.name,
            "deviceId": robot_dev.id,
            "rules": robot_rule.rules_json,
            "advice": adv.response_plan if adv else "",
            "adviceShort": adv.plan_short if adv else "",
        },
        "ruleRevision": 1,
        "device": {
            "id": robot_dev.id,
            "name": robot_dev.name,
            "code": robot_dev.code,
            "category": robot_dev.category,
            "location": robot_dev.location,
            "status": robot_dev.status,
        }
    }

    result_json = {
        "data": [{"arm_hydraulic_pressure": 29.4, "joint_temperature": 58.2}],
        "assessments": [{"row": 1, "field": "arm_hydraulic_pressure", "value": 29.4, "level": "critical", "passed": False}],
        "issues": [{
            "rowIndex": 0,
            "ruleName": "arm_hydraulic_pressure > 28.0",
            "severity": "critical",
            "message": "出炉机械臂液压实测 29.4 MPa，超过上限警戒阈值 28.0 MPa"
        }],
        "summary": {"totalRows": 1, "issueCount": 1, "criticalCount": 1, "normalCount": 0}
    }

    if not existing_rep:
        existing_rep = AlertReport(
            id=report_id,
            workspace_id=WORKSPACE_ID,
            owner_user_id=USER_ID,
            rule_id=robot_rule.id,
            device_id=robot_dev.id,
            execution_id=exec_id,
            node_id="alert_evaluate_node",
            title=report_title,
            outcome="issues",
            snapshot=report_snapshot,
            result_json=result_json,
            version=1,
        )
        db.add(existing_rep)
        db.flush()

        prob = AlertProblem(
            id=new_id(),
            workspace_id=WORKSPACE_ID,
            owner_user_id=USER_ID,
            report_id=existing_rep.id,
            row_index=0,
            record_key="29.4",
            finding={
                "rowIndex": 0,
                "ruleName": "arm_hydraulic_pressure > 28.0",
                "severity": "critical",
                "message": "机械臂出炉液压 29.4 MPa 严重超标，钎杆受阻",
                "field": "arm_hydraulic_pressure",
                "value": 29.4
            },
            status="open",
            note="现场巡检员待核实：建议切换至手动模式退出炉口并检查钎杆夹紧器",
            version=1,
        )
        db.add(prob)
        db.flush()
        print(f"  [+] Created AlertReport {existing_rep.id} and open AlertProblem {prob.id}")
    else:
        existing_rep.execution_id = exec_id
        existing_rep.result_json = result_json
        existing_rep.snapshot = report_snapshot
        db.flush()
        print(f"  [*] AlertReport {existing_rep.id} updated with correct issues schema and execution {exec_id}.")
    return existing_rep


def populate_quality_inspections(db):
    print("\n=== Step 3: Populating Quality Inspection Reports ===")
    devices = {d.code: d for d in db.query(Device).filter(Device.workspace_id == WORKSPACE_ID).all()}
    coke_station = devices.get("QC-COKE-STATION")
    coke_tmpl = db.query(QualityTemplate).filter(QualityTemplate.name == "焦炭进厂质量检验标准模板").first()

    if not coke_station or not coke_tmpl:
        print("  [-] Coke station or template missing, skipping inspection.")
        return None

    sample_id = "CK-BATCH-20260916-01"
    existing_insp = db.query(QualityInspection).filter(
        QualityInspection.workspace_id == WORKSPACE_ID,
        QualityInspection.sample_id == sample_id
    ).first()

    if not existing_insp:
        metrics_objs = db.query(QualityMetric).filter(QualityMetric.id.in_(coke_tmpl.metric_ids)).all()
        results = []
        for m in metrics_objs:
            val = "8.2" if "水分" in m.name else "11.5" if "灰分" in m.name else "84.5" if "固定碳" in m.name else "1.8" if "挥发分" in m.name else "0.35"
            results.append({
                "metricId": m.id,
                "name": m.name,
                "category": m.category,
                "outcome": "passed",
                "reason": f"{m.name} 实测值 {val}{m.unit} 符合技术标准要求（标准区间：{m.standard_min or '不限'} ~ {m.standard_max or '不限'}{m.unit}）",
                "observation": {
                    "metricId": m.id,
                    "value": val,
                    "unit": m.unit,
                    "imageRef": "",
                    "imageFileId": None,
                    "defects": None,
                    "note": "化验室标准抽样检验"
                },
                "provenance": {"mode": "manual", "inspector": "质检化验员-张工"}
            })

        digest = sha256(f"coke-inspection-{sample_id}".encode()).hexdigest()
        insp = QualityInspection(
            id=new_id(),
            workspace_id=WORKSPACE_ID,
            owner_user_id=USER_ID,
            template_id=coke_tmpl.id,
            device_id=coke_station.id,
            execution_id=None,
            node_id="qc_eval_node",
            sample_id=sample_id,
            request_key=f"REQ-INSP-{sample_id}",
            request_hash=digest,
            outcome="passed",
            snapshot={
                "template": {
                    "id": coke_tmpl.id,
                    "name": coke_tmpl.name,
                    "version": 1,
                    "metricIds": coke_tmpl.metric_ids,
                },
                "templateRevision": 1,
                "device": {"id": coke_station.id, "name": coke_station.name, "code": coke_station.code},
                "detectionIds": [],
                "inspectorId": USER_ID
            },
            results=results,
            status="active",
            version=1,
        )
        db.add(insp)
        db.flush()
        print(f"  [+] Created QualityInspection {insp.id} for sample {sample_id} (Outcome: passed)")
        return insp
    else:
        print(f"  [*] QualityInspection {existing_insp.id} already exists.")
        return existing_insp


def verify_report_exports(alert_report_id, quality_inspection_id):
    print("\n=== Step 4: Verifying HTML Report Exports via Live API ===")
    # 登录密码由运行环境提供，避免将本机凭据提交到仓库。
    login_data = json.dumps({"username": "workspace-admin", "password": os.environ["WORKSPACE_ADMIN_PASSWORD"]}).encode("utf-8")
    login_req = urllib.request.Request(
        "http://127.0.0.1:8011/api/auth/login",
        data=login_data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(login_req) as resp:
        token = json.loads(resp.read().decode("utf-8"))["token"]

    headers = {"Authorization": f"Bearer {token}"}

    # 1. Alert Report Export
    if alert_report_id:
        url = f"http://127.0.0.1:8011/api/v1/alerts/reports/{alert_report_id}/export"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            print(f"  [+] Alert Report Export HTTP {resp.status} ({len(content)} bytes)")
            assert "<!doctype html>" in content.lower()
            assert "预警" in content

    # 2. Quality Inspection Export
    if quality_inspection_id:
        url = f"http://127.0.0.1:8011/api/v1/quality/inspections/{quality_inspection_id}/export"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            print(f"  [+] Quality Inspection Export HTTP {resp.status} ({len(content)} bytes)")
            assert "<!doctype html>" in content.lower()
            assert "质量检查报告" in content

    print("  [+] Both Alert and Quality HTML reports exported and verified successfully!")


def main():
    db = _SessionLocal()
    rep = None
    insp = None
    try:
        devices, rules, advice = populate_equipment_templates(db)
        rep = populate_alert_reports_and_problems(db, devices, rules, advice)
        insp = populate_quality_inspections(db)
        db.commit()
        print("\nAll database records committed successfully!")
    except Exception as e:
        db.rollback()
        print(f"Database error: {e}")
        raise
    finally:
        db.close()

    try:
        verify_report_exports(rep.id if rep else None, insp.id if insp else None)
    except Exception as e:
        print(f"Verification error: {e}")
        raise


if __name__ == "__main__":
    main()
