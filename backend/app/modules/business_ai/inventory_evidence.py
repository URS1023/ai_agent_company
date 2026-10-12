"""纯库存概况回答只依据本轮库存回执，避免把物料目录误解为库存事实。"""
from __future__ import annotations

from collections import Counter
import re

from sqlalchemy import select

from app.models import Message, ToolCall
from app.modules.analytics.inventory_identity import inventory_material_counts
from .alert_evidence import _text


def _simple_inventory_request(snapshot: dict) -> bool:
    """只处理当前概况、单位或预警；明细、文件、历史及其他业务保留正常回答链。"""
    task = snapshot.get("business_task") or {}
    if task.get("required_deliveries") or snapshot.get("required_deliveries"):
        return False
    request = str(snapshot.get("user_request") or snapshot.get("submitted_content") or "")
    # “无需生成文件”属于概况限制，不应被文件排除条件误判为制作请求。
    request = re.sub(r"(?:无需|不用|不需要|不要)(?:再|额外)?(?:生成|创建|制作|导出|输出|下载)?"
                     r"(?:任何)?(?:文件|文档|Excel|表格|PPT|PDF|Word)", "", request, flags=re.I)
    if "库存" not in request or not re.search(r"概况|概览|单位|预警", request):
        return False
    if not re.search(r"查看|查询|读取|获取|核对|检查|看一下|看看|看下|帮我看|帮忙看", request):
        return False
    return not re.search(
        r"明细|逐条列|逐项列|文件|附件|Excel|xlsx|PPT|幻灯片|Word|docx|PDF|表格|大屏|看板|"
        r"历史|趋势|过去|昨天|昨日|上周|本周|上月|本月|去年|今年|期间|同比|环比|预测|变化|"
        r"采购|补货|入库|出库|盘点|调拨|订单|成本|金额|销售|财务|供应商|ERP|"
        r"设备|巡检|质检|维修|工作流|进度|"
        r"解释|含义|定义|概念|设计|实现|接入|代码|原理|教程|举例|区别|为什么|怎么用|如何用|方案|"
        r"无库存|没有库存|零库存|缺货|空仓|物料档案|仓库目录|仓库列表|"
        r"\d{4}[-年/]\d{1,2}", request, re.I,
    )


def compact_inventory_message(view: dict) -> str:
    """从原始行重新计算本页事实，不采用模型文字或目录数量推断库存。"""
    rows = view["data"]
    statistics = view.get("statistics") or {}
    count = len(rows)
    offset = statistics.get("offset", 0)
    material_counts = inventory_material_counts(rows, (view.get("retrievalEvidence") or {}).get("materialIdentity"))
    if not rows:
        return ("本次库存查询的当前页未返回记录。空页不能证明全库没有库存，"
                "也不能据此认定物料档案中的物料均无库存；本页无法核对单位或判断库存预警。")
    known_material_text = (f"已确认 {material_counts['knownMaterialCount']} 种物料身份，"
                           if material_counts["knownMaterialCount"] else "尚无可核实的完整物料身份，")
    material_text = (f"涉及 {material_counts['materialCount']} 种物料（按完整已授权引用身份去重）。"
                     if material_counts["materialCountKnown"] else
                     "物料种数无法完整确认；" + known_material_text
                     + f"{material_counts['unknownMaterialRows']} 条库存记录的物料身份未知。")
    lines = [f"已读取库存的本页快照：**{count} 条库存记录**，" + material_text
             + (f"本页从第 {offset + 1} 条开始，" if isinstance(offset, int) and offset > 0 else "")
             + ("还有后续记录；" if view.get("truncated") else "") + "本页记录数不作为全库总量。"]
    stock_units = Counter(row.get("库存计量单位") for row in rows)
    distribution = "、".join(f"{_text(unit) if unit is not None else '单位未知'} {number} 条"
                              for unit, number in stock_units.items())
    comparisons = Counter("未知" if row.get("库存计量单位") is None or row.get("物料计量单位") is None else
                          "一致" if row["库存计量单位"] == row["物料计量单位"] else "不一致" for row in rows)
    comparison_text = "、".join(f"{label} {comparisons[label]} 条" for label in ("一致", "不一致", "未知")
                                if comparisons[label])
    lines.append(f"**计量单位：**{distribution}。逐条核对同一记录的库存单位与物料单位：{comparison_text}。"
                 "这不表示不同记录的单位相同；不同物料或不同单位的库存数量不可合计。")
    missing_threshold = sum(row.get("安全库存") is None for row in rows)
    missing_quantity = sum(row.get("实际可用库存") is None for row in rows)
    unknown = sum(row.get("安全库存") is None or row.get("实际可用库存") is None for row in rows)
    conclusions = []
    if unknown:
        reasons = []
        if missing_threshold:
            reasons.append(f"{missing_threshold} 条缺少安全库存阈值")
        if missing_quantity:
            reasons.append(f"{missing_quantity} 条缺少可用库存数量")
        conclusions.append(f"{unknown} 条为未知（{'、'.join(reasons)}）")
    if count > unknown:
        conclusions.append(f"{count - unknown} 条待核对物料级阈值的比较范围和单位")
    lines.append("**库存预警：**" + "；".join(conclusions) + "。不能据此判定库存正常或直接形成补货结论。")
    return "\n\n".join(lines)


def verified_inventory_reply(db, run, content: str, status: str, error: dict | None) -> dict | None:
    """仅接管单份当前库存回执的概况；失败、范围不全及历史回执不能冒充本次成功。"""
    snapshot = run.context_snapshot or {}
    if not _simple_inventory_request(snapshot):
        return None
    calls = list(db.scalars(select(ToolCall).where(
        ToolCall.workspace_id == run.workspace_id, ToolCall.run_id == run.id,
    ).order_by(ToolCall.started_at, ToolCall.id)))
    # 通用查询或其他业务工具可能承载用户定制范围，不用库存模板覆盖其有效回答。
    if any(call.tool_name not in {"query_inventory_data", "current_datetime", "discover_data_sources", "list_business_data_sources"}
           and call.status == "completed" and isinstance(call.result_json, dict)
           and call.result_json.get("status") == "success" for call in calls):
        return None
    inventory_calls = [call for call in calls if call.tool_name == "query_inventory_data"
                       and isinstance(call.arguments_json, dict)
                       and call.arguments_json.get("kind", "inventory") == "inventory"]
    message = db.get(Message, snapshot.get("assistant_message_id")) if snapshot.get("assistant_message_id") else None
    parts = [part for part in (message.parts_json or []) if isinstance(part, dict)] if message else []
    arguments_by_id = {part.get("id"): part.get("arguments") if isinstance(part.get("arguments"), dict) else {} for part in parts
                       if part.get("type") == "tool_call" and part.get("name") == "query_inventory_data"}
    failed = any(part.get("type") == "tool_result" and part.get("tool") == "query_inventory_data"
                 and arguments_by_id.get(part.get("id"), {}).get("kind", "inventory") == "inventory"
                 and (not isinstance(part.get("result"), dict) or part["result"].get("status") != "success") for part in parts)
    if not inventory_calls and not failed:
        return None
    receipts = []
    for call in inventory_calls:
        result = call.result_json if isinstance(call.result_json, dict) else {}
        value = result.get("data") if isinstance(result.get("data"), dict) else {}
        if (call.status != "completed" or result.get("status") != "success" or value.get("success") is not True
                or not isinstance(value.get("data"), list) or any(not isinstance(row, dict) for row in value["data"])
                or not value.get("queryId")
                or (value.get("retrievalEvidence") or {}).get("kind") != "inventory"):
            failed = True
        else:
            receipts.append(value)
    if failed or not receipts:
        return {
            "content": "本轮库存读取尚未完成，无法核实本次请求范围内的计量单位与库存预警。"
                       "已有历史结果或物料、仓库目录不能代替本轮库存记录。",
            "status": "partial" if status == "completed" else status,
            "error": error or {"code": "inventory_evidence_incomplete", "message": "缺少本轮完整成功的库存回执。"},
        }
    if len(receipts) != 1:
        # 多页、多个筛选或多个来源需要独立覆盖判断，不把任意一页当作整个任务结果。
        return None
    return {"content": compact_inventory_message(receipts[0]), "status": status, "error": error}
