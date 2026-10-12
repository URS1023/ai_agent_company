"""Bounded, metadata-first business overview; no LLM SQL or guessed joins."""
import re
import time
from decimal import Decimal, InvalidOperation

from app.errors import ValidationError
from app.modules.analytics.semantic import is_business_table, SENSITIVE_MARKERS


FACETS = {
    "收款与回款": ("收款", "回款", "应收", "receivable"),
    "付款与应付": ("付款", "支付", "应付", "payment", "payable"),
    "发票": ("发票", "invoice"),
    "财务成本": ("财务成本", "成本报表", "caiwuchengben", "costreport"),
    "财务利润": ("财务利润", "利润报表", "caiwulirun", "profitreport"),
    "财务核算": ("总账", "凭证", "generalledger", "voucher", "finance"),
    "合同": ("合同", "contract"),
    "销售订单": ("销售订单", "saleorder", "salesorder"),
    "采购订单": ("采购订单", "purchaseorder", "purchasingorder"),
}


def sample_amount_totals(rows: list[dict], columns: list[dict]) -> dict:
    """Keep distinct monetary columns distinct, and label their limited scope."""
    totals = {}
    for column in columns:
        name = column['columnName']
        if not re.search(r"金额|amount", name + str(column.get('comment') or ''), re.I):
            continue
        values = []
        for row in rows:
            value = row.get(name)
            if value is None or isinstance(value, bool):
                continue
            try:
                number = Decimal(str(value))
                if number.is_finite():
                    values.append(number)
            except InvalidOperation:
                continue
        if values:
            totals[name] = {"label": column.get('comment') or name, "sum": str(sum(values)), "valueCount": len(values)}
    return {"scope": "本次返回样本小计，不是全库汇总；申请、已付和未付金额不可互相替代", "amounts": totals}


def _receivable_table(table: dict) -> bool:
    """判断表是否真的属于“收款/应收”口径（只看表身份，不看字段）。

    中文用否定前瞻排除“收款单位/人/账户”等付款单据里的字段名；英文要求
    cash/payment/customer/debt 等金融限定词，避免把 warehouse receipt（入库单）
    或 dataset collection（数据集）误判为收款表。
    """
    # A warehouse receipt is an inventory record, and a collection can be a
    # dataset. Only financial context makes these English words receivables.
    # Inspect table identity, never incidental fields such as 收款单位 on a
    # supplier payment record.
    # 只看表身份（表名 + 表注释），不看字段：付款单上的“收款单位”字段不代表该表是
    # 收款表；英文 receipt/collection 在库存与数据集语境下也不是应收，因此中文用
    # 否定前瞻排除“收款单位/人/账户”，英文要求 cash/payment/customer 等金融限定词。
    text = (str(table.get("tableName") or "") + " " + str(table.get("tableComment") or "")).lower()
    if re.search(r"收款(?!单位|人|账户|账号|银行)|回款|应收|receivable", text):
        return True
    compact = re.sub(r"[^a-z]", "", text)
    return bool(re.search(
        r"(?:cash|payment|customer)receipts?|receipts?(?:cash|payment|customer)"
        r"|(?:cash|payment|customer|debt)collections?",
        compact,
    ))


def wants_overview(question: str) -> bool:
    """判断这句话是否应该走“多主题概览”而不是常规取数。

    只有“短主题请求”（如“财务数据”“财务成本”）才返回 True：一旦出现统计/金额/时间/
    编号/明细等限定词就交给正常查询链路，避免固定概览 SQL 静默丢掉用户的条件。
    已知边界：判断完全基于正则与白名单主题词，用户用未登记的同义说法（例如“资金面
    情况”）会被判为 False 而走常规链路——这是刻意选择的保守方向（宁可多查一次，
    也不能把带条件的请求降级成固定概览）。
    """
    text = str(question or "").strip()
    if re.search(r"统计|汇总|多少|总额|金额|数量|利润|趋势|同比|环比|排名|SELECT\b|\d|单号|编号|客户|供应商|今天|昨天|本月|上月|今年|最近|明细|日期", text, re.I):
        return False
    # Accept a short topic request with or without a verb. Other words retain
    # their meaning by going through the normal query path instead of being
    # silently dropped by the fixed overview query.
    text = re.sub(r"[\s，,。！？!?：:]", "", text)
    text = re.sub(r"^(?:请|帮我|给我|我想|麻烦)*", "", text)
    text = re.sub(r"^(?:查看一下|查询一下|了解一下|查看|看看|看下|有哪些|浏览|查询|查一下|看一下)+", "", text)
    text = re.sub(r"^(?:当前|现有|全部|所有)", "", text)
    text = re.sub(r"(?:的数据|数据|情况|概览)", "", text)
    topic = r"(?:财务成本|财务核算|财务|资金支付|收款|回款|应收|付款|支付|应付|发票|成本|核算|总账|凭证|合同|销售订单|采购订单|订单)"
    return bool(re.fullmatch(topic + r"(?:(?:以及|和|与|及|、)?" + topic + r")*", text))


def _requested_facets(question: str) -> dict:
    """把问题映射到 FACETS 里的主题子集；只返回本次真正被要求的口径。

    规则：命中主题别名即选中；“财务”泛请求会展开为除订单外的全部财务主题，
    但一旦用户明确说了具体订单类型（销售/采购）就只保留该类型。返回的是
    ``{主题: 别名}``，别名用于后续在表名/表注释里匹配。
    """
    text = str(question or "").lower()
    markers = {
        "收款与回款": ("收款", "回款", "应收", "receivable", "cash receipt", "customer receipt"),
        "付款与应付": ("付款", "支付", "应付", "payment", "payable"),
        "发票": ("发票", "invoice"),
        "财务成本": ("成本", "cost"),
        "财务利润": ("利润", "profit"),
        "财务核算": ("核算", "总账", "凭证", "ledger", "voucher"),
    }
    requested = {label for label, aliases in markers.items() if any(term in text for term in aliases)}
    broad_finance = "财务" in text and (
        not requested or bool(re.search(r"财务(?:数据|概览)?(?:和|与|及|、|，)", text))
    )
    if broad_finance:
        requested.update(label for label in FACETS if "订单" not in label)
    if "合同" in text or "contract" in text:
        requested.add("合同")
    if "订单" in text:
        specific_orders = {label for label in ("销售订单", "采购订单") if label in text}
        requested.update(specific_orders or {"销售订单", "采购订单"})
    return {label: aliases for label, aliases in FACETS.items() if label in requested}


def overview_catalog(tables: list[dict], question: str) -> list[dict]:
    """为每个被要求的主题挑选一张代表表，并保留全部候选作为覆盖证据。

    选择规则：表名/表注释命中主题别名（收款主题另有更严格的表身份判断）；排序时把
    明细/子表（detail/product/item/明细）排在后面，优先取表头表；已被前面主题选中的
    表不再重复选，避免一个主题“吃掉”同一张表。
    返回 [{topic, candidates, selected}]，``selected`` 可能为 None 表示授权目录里
    确实没有匹配表——此时必须如实说明未匹配，不能拿其他主题的表顶替。
    """
    facets = _requested_facets(question)
    result = []
    seen = set()
    for label, aliases in facets.items():
        matches = [table for table in tables if is_business_table(table.get("tableName", "")) and (
            _receivable_table(table) if label == "收款与回款" else any(
                term in (str(table.get("tableName", "")) + " " + str(table.get("tableComment", ""))).lower() for term in aliases
            ))]
        # Prefer header tables, but preserve every candidate as coverage evidence.
        matches.sort(key=lambda t: (bool(re.search(r"detail|product|item|明细", t["tableName"] + str(t.get("tableComment", "")), re.I)), t["tableName"]))
        picked = next((t for t in matches if t["tableName"] not in seen), None)
        if picked:
            seen.add(picked["tableName"])
        result.append({"topic": label, "candidates": [t["tableName"] for t in matches], "selected": picked})
    return result


def execute_overview(ctx, source, question: str) -> dict:
    """按主题逐个取样，生成**有界**的业务概览（不使用模型生成的 SQL）。

    实现约束：每个主题只取至多 5 行明细样本，通过正常的 execute_query 走同一套授权、
    只读与行数上限；敏感字段（SENSITIVE_MARKERS）不进入选择列表，排序时优先金额/
    数量/日期/状态等业务列；进入循环前先算好 20 秒预算与最多 8 个主题的上限，
    超预算的主题标记 budget_exhausted 而不是继续查询。
    单次查询失败时直接抛出中断整批：失败/超时之后继续查询只会得到半真半假的概览。
    返回 summary/results/coverage/guidance；guidance 明确“样本不等于全库”“不能用
    销售订单替代完整财务”等口径约束，避免读者把样本当结论。
    """
    from app.modules.analytics.metadata import source_metadata
    from app.modules.analytics.queries import execute_query
    from app.modules.analytics.schemas import QueryInput
    tables = source_metadata(ctx, source).tables_json
    catalog = overview_catalog(tables, question)
    results = []
    deadline = time.monotonic() + 20
    for item in catalog:
        table = item["selected"]
        entry = {"topic": item["topic"], "candidateTables": item["candidates"], "status": "no_matching_metadata"}
        results.append(entry)
        if not table:
            continue
        entry["tableName"] = table["tableName"]
        if time.monotonic() >= deadline or sum(x["status"] == "sampled" for x in results) >= 8:
            entry["status"] = "budget_exhausted"
            continue
        readable = [c for c in table.get("columns", []) if not any(
            marker in (c.get("columnName", "") + " " + str(c.get("comment", ""))).lower() for marker in SENSITIVE_MARKERS)]
        readable.sort(key=lambda c: (
            not bool(re.search(r"金额|成本|利润|收入|数量|日期|时间|状态|名称|单号|amount|total|cost|profit|date|time|status|name|number", c['columnName'] + str(c.get('comment', '')), re.I)),
            bool(re.search(r"createdBy|modifiedBy|owner|department|^id$|Id$", c['columnName'])), c['columnName'],
        ))
        columns = [c['columnName'] for c in readable]
        if not columns:
            entry["status"] = "no_readable_columns"
            continue
        quote = (lambda s: '"' + s.replace('"', '""') + '"') if source.provider == "postgres" else (lambda s: '`' + s.replace('`', '``') + '`')
        if source.provider not in {"mysql", "postgres"}:
            raise ValidationError("该数据源暂不支持多主题概览，请指定具体业务问题")
        sql = "SELECT " + ",".join(quote(c) for c in columns[:12]) + " FROM " + quote(table["tableName"])
        if "isDeleted" in columns:
            sql += " WHERE " + quote("isDeleted") + " = 0"
        sql += " LIMIT 5"
        try:
            view = execute_query(ctx, QueryInput(datasourceId=source.id, query=sql, mode="sql", maxRows=5, needAnalysis=False, needChart=False))
        except Exception:
            # Never continue a batch after a failed/timeout/disconnected query.
            raise
        entry.update(status="sampled", data=view.data, columns=[column.model_dump(mode="json") for column in view.columns], queryId=view.queryId, scope="至多5行明细样本，不代表全表数量或金额", sampleStatistics=sample_amount_totals(view.data, table.get('columns') or []))
    sampled = sum(x['status'] == 'sampled' for x in results)
    unmatched = bool(results) and all(x['status'] == 'no_matching_metadata' for x in results)
    # These alternatives come only from the authorized catalog. Do not execute
    # unrelated topics merely because the requested one has no matching table.
    available_topics = [item['topic'] for item in overview_catalog(tables, "财务") if item['selected']] if unmatched else []
    summary = ("当前授权目录未匹配到本次所需的" + "、".join(x['topic'] for x in results) + "数据"
               if unmatched else f"已查看 {sampled} 个业务主题的有限样本")
    return {"summary": summary, "results": results, "availableTopics": available_topics,
            "coverage": {"authorizedTableCount": len(tables), "candidateTableCount": len({name for x in results for name in x['candidateTables']})},
            "guidance": "逐主题说明查询表和未查询候选。不能用销售订单代替完整财务，不能把样本行数说成全库总数，不能将缺失成本当作0或计算利润；仅说明当前授权目录未匹配，不能断言不存在业务数据或金额为0。入库、收货、签收和付款/支付记录不能顶替收款回款。无匹配时不重复查询，不要求用户提供表名或字段；可按 availableTopics 中已有财务主题提出一个简短选择问题，这些选项只来自目录，尚未取数。字段编码不猜测，结果未选日期不代表源表无日期字段。"}
