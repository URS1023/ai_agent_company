"""Existing Python code-node configuration, not a new backend service.

Inputs contain SELECT results and a versioned, device-specific requirement snapshot.
Never infer a measurement from the copied requirement field. No I/O is performed.
"""
import calendar
import re
from datetime import datetime, timedelta
from decimal import Decimal


def clean(value):
    return str(value or "").strip()


def date(value):
    try:
        result = datetime.fromisoformat(clean(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=None) if result.tzinfo is None else None
    except (ValueError, TypeError):
        return None


def next_date(start, cycle):
    code = clean(cycle)
    hours = {"1": 2, "2": 4, "3": 8, "4": 12, "5": 24, "6": 168, "13": 168}
    months = {"7": 1, "8": 3, "9": 6, "10": 12, "14": 1, "15": 3, "16": 6, "17": 12}
    if code in hours:
        return start + timedelta(hours=hours[code])
    if code in months:
        total = start.year * 12 + start.month - 1 + months[code]
        year, month = total // 12, total % 12 + 1
        return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))
    return None


def number(value, unit):
    """Accept a scalar with an explicit matching unit; never mine prose for digits."""
    text = clean(value).replace("℃", "°C")
    found = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*" + re.escape(unit), text, re.I)
    return Decimal(found.group(1)) if found else None


def amount_check(actual, expected):
    pattern = r"([0-9]+(?:\.[0-9]+)?)(?:\s*[-~～至]\s*([0-9]+(?:\.[0-9]+)?))?\s*(kg|g)"
    plan = re.fullmatch(pattern, clean(expected), re.I)
    measured = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(kg|g)", clean(actual), re.I)
    if not plan or not measured:
        return None
    factor = Decimal(1000) if plan.group(3).lower() == "kg" else Decimal(1)
    low, high = Decimal(plan.group(1)) * factor, Decimal(plan.group(2) or plan.group(1)) * factor
    actual_g = Decimal(measured.group(1)) * (1000 if measured.group(2).lower() == "kg" else 1)
    return low <= actual_g <= high


def base(spec, plan, item, scene):
    return {
        "recordKey": spec["deviceKey"] + ":" + plan.get("planId", scene) + ":" + item["itemId"],
        "deviceKey": spec["deviceKey"], "deviceName": spec["deviceName"], "category": scene,
        "planId": plan.get("planId", ""), "planName": plan.get("planName", ""),
        "itemId": item["itemId"], "itemName": item.get("name") or item.get("point") or "周期检查",
        "indicator": item.get("indicators", ""), "requirement": item.get("requirementText") or item.get("content", ""),
        "asOf": spec["asOf"], "sourceRecordId": "", "sourceTime": "", "actual": "",
        "status": "pending", "reason": "", "basis": "", "alertCount": 0, "pendingCount": 0,
    }


def finish(row, status, reason, basis=""):
    row.update(status=status, reason=reason, basis=basis,
               alertCount=int(status == "warning"), pendingCount=int(status == "pending"))
    return row


def inspection(spec, plan, records, now):
    selected = [r for r in records if r.get("deviceKey") == spec["deviceKey"]
                and clean(r.get("planName")) == clean(plan["planName"])]
    output = []
    cycle = base(spec, plan, {"itemId": "cycle", "name": "巡检周期"}, "inspection")
    cycle.update(cycleCode=plan.get("cycleCode"), requirement=plan.get("cycleLabel", "未知周期"))
    latest = max((date(r.get("lastCompleted")) for r in selected if date(r.get("lastCompleted"))
                  and date(r.get("lastCompleted")) <= now), default=None)
    due = next_date(latest, plan.get("cycleCode")) if latest else None
    cycle.update(lastCompleted=str(latest or ""), dueAt=str(due or ""))
    ambiguous = plan.get("ambiguousName", False)
    if ambiguous:
        output.append(finish(cycle, "pending", "源记录只保存计划名称，该名称对应多个有效计划，不能唯一关联"))
    elif due:
        output.append(finish(cycle, "warning" if now > due else "normal",
                             "按副本最后一次已完成巡检时间和本设备计划周期计算；超过应检时间" if now > due else "未到下次巡检时间"))
    else:
        output.append(finish(cycle, "pending", "缺少有效完成时间或计划周期，不能推算应检时间"))
    for item in plan["items"]:
        row = base(spec, plan, item, "inspection")
        matched = [r for r in selected if clean(r.get("name")) == clean(item["name"])
                   and clean(r.get("indicators")) == clean(item["indicators"])]
        if ambiguous or len(matched) != 1:
            output.append(finish(row, "pending", "计划或检查项关联不唯一" if ambiguous or matched else "最新巡检记录中缺少本项结果"))
            continue
        actual = matched[0]
        row.update(sourceRecordId=actual.get("itemRecordId") or "", taskId=actual.get("taskId"),
                   sourceTime=actual.get("recordedAt") or "", actual=actual.get("actual") or "",
                   originalResult=actual.get("result"), recordedContent=actual.get("content") or "")
        if clean(actual.get("result")) == "2":
            output.append(finish(row, "warning", "本设备最新巡检记录明确记为异常", "原系统人工巡检结果"))
            continue
        if clean(actual.get("taskStatus")) != "1" or not date(actual.get("recordedAt")):
            output.append(finish(row, "pending", "本次巡检尚未确认完成或缺少记录时间"))
            continue
        if clean(item.get("content")) and clean(actual.get("content")) != clean(item.get("content")):
            output.append(finish(row, "pending", "记录中的标准与当前配置不一致，需确认标准版本；不将标准字段当测量值"))
            continue
        rule = item.get("numericRule")
        if rule:
            measured = number(actual.get("actual"), rule["unit"])
            row.update(lowerBound=rule["min"], unit=rule["unit"])
            if measured is None:
                output.append(finish(row, "pending", "有明确下限但缺少带单位的实际测量值；人工正常标记不能替代数值校验"))
            else:
                output.append(finish(row, "normal" if measured >= Decimal(str(rule["min"])) else "warning",
                                     "实际测量值与本项下限比较（包含边界）", "实际描述字段中的单一数值"))
        elif item.get("manualReason"):
            output.append(finish(row, "pending", item["manualReason"]))
        elif not clean(item.get("requirementText") or item.get("content")):
            output.append(finish(row, "pending", "源计划没有提供判断标准，不能自动判断合格"))
        elif clean(actual.get("result")) == "1":
            output.append(finish(row, "normal", "本项按相同标准由原系统人工确认正常；并非自动测量", "原系统人工巡检结果"))
        else:
            output.append(finish(row, "pending", "缺少明确的正常/异常检查结果"))
    return output


def maintenance(spec, plan, records, now):
    output = []
    for item in plan["items"]:
        row = base(spec, plan, item, "maintenance")
        row.update(pointCode=item.get("code", ""), cycleDays=item.get("days"),
                   planCycle=plan.get("planCycle"), lubricant=item.get("lubricant"), amount=item.get("amount"),
                   method=item.get("method"), oilChangeCycle=item.get("oilChangeCycle"),
                   oilChangeAmount=item.get("oilChangeAmount"))
        row["requirement"] = "；".join(["润滑周期：" + str(item.get("days") or "未填") + "天",
            "油品：" + clean(item.get("lubricant")), "用量：" + clean(item.get("amount")),
            "方法：" + clean(item.get("method")), "换油周期：" + clean(item.get("oilChangeCycle"))])
        matches = [r for r in records if r.get("deviceKey") == spec["deviceKey"]
                   and clean(r.get("planName")) == clean(plan["planName"])
                   and (clean(r.get("code")) == clean(item["code"]) if clean(item.get("code"))
                        else clean(r.get("point")) == clean(item.get("point")))]
        if plan.get("ambiguousName") or not matches:
            output.append(finish(row, "pending", "计划关联不唯一" if plan.get("ambiguousName") else "没有与本设备、本计划、本润滑点匹配的保养明细；不能推算逾期"))
            continue
        matches.sort(key=lambda r: clean(r.get("observedAt")), reverse=True)
        actual = matches[0]
        if sum(clean(r.get("observedAt")) == clean(actual.get("observedAt")) for r in matches) > 1:
            output.append(finish(row, "pending", "最新保养明细存在重复关联，需确认"))
            continue
        completed = date(actual.get("completedAt"))
        row.update(sourceRecordId=actual.get("itemRecordId"), sourceTime=actual.get("completedAt") or "",
                   actual=actual.get("amount") or "", recordedLubricant=actual.get("lubricant"),
                   recordedMethod=actual.get("method"), originalStatus=actual.get("pointStatus"))
        if clean(actual.get("pointStatus")) != "1" or clean(actual.get("taskStatus")) != "1" or not completed or completed > now:
            output.append(finish(row, "pending", "缺少本点已完成的保养状态和有效完成时间，不能用创建时间代替"))
            continue
        days = item.get("days")
        due = completed + timedelta(days=int(days)) if days and int(days) > 0 else None
        row["dueAt"] = str(due or "")
        errors, missing = [], []
        if due and now > due:
            errors.append("超过本点润滑周期")
        if not due:
            missing.append("未配置有效润滑周期")
        # Execution oil/amount/method fields may be copied instructions, not actual evidence.
        for key, label in (("lubricant", "油品"), ("method", "保养方法")):
            if clean(actual.get(key)) and clean(item.get(key)) and clean(actual[key]) != clean(item[key]):
                errors.append(label + "与本点要求不一致")
            else:
                missing.append(label + "字段可能为复制要求，需确认实际执行证据")
        measured_ok = amount_check(actual.get("amount"), item.get("amount"))
        if clean(actual.get("amount")) == clean(item.get("amount")) or measured_ok is None:
            missing.append("缺少可独立核对的实际加注量")
        elif measured_ok is False:
            errors.append("加注量不在本点要求范围内")
        if clean(item.get("oilChangeCycle")):
            missing.append("换油周期有要求，但缺少独立换油完成日期")
        row["pendingDetails"] = "；".join(missing)
        output.append(finish(row, "warning" if errors else "pending" if missing else "normal",
                             "；".join(errors + missing) or "本点要求均已核实"))
    return output


def repair(spec, records, now):
    output = []
    for actual in records:
        if actual.get("deviceKey") != spec["deviceKey"]:
            continue
        row = base(spec, {"planId": actual.get("planId") or "repair", "planName": "维修任务"},
                   {"itemId": actual["taskId"], "name": actual.get("point") or "维修任务",
                    "content": actual.get("content") or ""}, "repair")
        status = clean(actual.get("taskStatus"))
        deadline, completed = date(actual.get("deadline")), date(actual.get("completedAt"))
        row.update(sourceRecordId=actual["taskId"], deadline=actual.get("deadline"),
                   sourceTime=actual.get("completedAt") or "", originalStatus=status,
                   acceptance=actual.get("acceptance"), installation=actual.get("installation"), historicalLate=False)
        if status == "3" and completed and completed <= now:
            row["historicalLate"] = bool(deadline and completed > deadline)
            row["acceptanceNote"] = "未记录验收/安装结果；是否适用需业务确认" if actual.get("acceptance") is None else "已记录"
            state = "warning" if clean(actual.get("acceptance")) == "2" or clean(actual.get("installation")) == "2" else "pending" if clean(actual.get("acceptance")) != "1" else "normal"
            reason = ("完成后仍有明确验收不合格或未安装记录" if state == "warning" else
                      "维修已完成；验收结果未记录，需确认验收适用性；历史超期不计当前逾期" if state == "pending" else
                      "维修已完成且已记录验收合格；历史超期不计当前逾期")
            output.append(finish(row, state, reason))
        elif status == "4" or (status in ("1", "2") and deadline and now > deadline):
            output.append(finish(row, "warning", "维修失败" if status == "4" else "超过本维修任务预计完成时间"))
        else:
            output.append(finish(row, "pending" if status not in ("1", "2") or not deadline else "normal",
                                 "缺少有效状态、完成日期或预计完成时间" if status not in ("1", "2") or not deadline else "维修任务尚未到期"))
    return output


def current_plans(device, records):
    """Rebuild configured plan requirements from the current read-only query.

    Snapshots in the canvas explain the saved configuration; they never override
    a live standard. New plans need an explicit binding to avoid expanding scope.
    """
    expected = {p["planId"]: p for p in device.get("plans", [])}
    result = {}
    cycle_labels = {1: "2小时", 2: "4小时", 3: "8小时", 4: "12小时", 5: "1天", 6: "1周",
                    7: "1个月", 8: "1季度", 9: "半年", 10: "1年"}
    for row in records:
        if row.get("deviceKey") != device["deviceKey"] or row.get("planId") not in expected:
            continue
        scene = "inspection" if clean(row.get("planType")) == "1" else "maintenance"
        if scene != expected[row["planId"]]["scene"]:
            continue
        plan = result.setdefault(row["planId"], {"planId": row["planId"], "planName": row["planName"],
            "scene": scene, "cycleCode": row.get("cycleCode"), "cycleLabel": cycle_labels.get(row.get("cycleCode"), "未填"),
            "planCycle": row.get("planCycle"), "ambiguousName": row.get("sameNameCount") != 1, "items": []})
        if not row.get("itemId"):
            continue
        item = {key: row.get(key) for key in ("itemId", "name", "indicators", "content", "point", "code", "days",
                "lubricant", "amount", "method", "oilChangeCycle", "oilChangeAmount")}
        if scene == "inspection":
            content, indicator = clean(item.get("content")), clean(item.get("indicators"))
            qualitative = ("正常", "无", "无变色", "是/否", "有/无")
            item["requirementText"] = content or (indicator if indicator in qualitative else "")
            if content in ("数值", "实时") or (not content and indicator not in qualitative):
                item["manualReason"] = "源计划只要求记录实时数值，未提供合格上下限，不能自行设置报警阈值"
            # A full-string scalar lower-bound requirement only; compound prose stays manual.
            lower = re.fullmatch(r"(?:在)?([0-9]+(?:\.[0-9]+)?)\s*(V|°C|℃)以上", content)
            if lower:
                item["numericRule"] = {"min": lower.group(1), "unit": lower.group(2).replace("℃", "°C")}
            elif "7-8m" in content:
                item["manualReason"] = "源要求写为距顶部7-8m，单位和测量位置需业务确认，不擅自改成厘米"
            elif re.search(r"\d|两格", content):
                item["manualReason"] = "包含数值或多项联合要求；缺少分别可核对的实际值，需逐项人工核实"
        plan["items"].append(item)
    return list(result.values())


def main(spec, inspectionRecords=None, maintenanceRecords=None, repairRecords=None, requirementRecords=None, **unused):
    if any(value for key, value in unused.items() if key.endswith("Truncated")):
        raise ValueError("源查询结果被截断，停止判定；请缩小查询范围，不能用部分数据生成完整报告")
    now = date(spec.get("asOf"))
    if not now:
        raise ValueError("回放基准必须是本地时间 YYYY-MM-DD HH:MM:SS")
    output = []
    selected = [d for d in spec["devices"] if not clean(spec.get("deviceKey")) or d["deviceKey"] == spec["deviceKey"]]
    if not selected:
        raise ValueError("该设备不在此工作流已配置的计划范围内，请从设备入口运行")
    for device in selected:
        current = {**device, "asOf": spec["asOf"]}
        plans = device.get("plans", []) if requirementRecords is None else current_plans(device, requirementRecords)
        active_ids = {p["planId"] for p in plans if p["items"]}
        if requirementRecords is not None:
            for expected in device.get("plans", []):
                if expected["planId"] not in active_ids:
                    row = base(current, expected, {"itemId": "scope", "name": "计划适用性"}, expected["scene"])
                    output.append(finish(row, "pending", "源库中该计划、设备关联、类别或检查项已变化/失效；不沿用旧要求，请重新确认绑定"))
        for plan in plans:
            if plan["scene"] == "inspection":
                output.extend(inspection(current, plan, inspectionRecords or [], now))
            elif plan["scene"] == "maintenance":
                output.extend(maintenance(current, plan, maintenanceRecords or [], now))
        if device.get("repairEnabled"):
            output.extend(repair(current, repairRecords or [], now))
    if len({row["recordKey"] for row in output}) != len(output):
        raise ValueError("设备计划检查项存在重复标识，停止保存避免覆盖结果")
    return {"data": output, "totalCount": len(output), "asOf": spec["asOf"],
            "sourceMode": "导入副本回放", "assessmentVersion": "device-specific-v1",
            "summary": {key: sum(r["status"] == key for r in output) for key in ("normal", "warning", "pending")}}
