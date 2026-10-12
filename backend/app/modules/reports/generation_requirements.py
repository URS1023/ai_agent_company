"""Keep business scope separate from template appearance and model suggestions."""
from __future__ import annotations

SCOPE_TERMS = {
    "finance": ("财务", "财会", "会计", "科目", "凭证", "营收", "收入", "利润", "成本", "应收", "应付", "现金流",
                "资金", "支付", "收款", "付款", "已付", "未付", "费用", "预算", "收支",
                "finance", "financial", "accounting", "revenue", "profit", "receivable", "payable", "payment", "fund", "expense", "cost"),
    "equipment": ("设备", "巡检", "保养", "维修", "equipment", "device", "maintenance"),
    "quality": ("质检", "质量", "检验", "quality"),
    "sales": ("销售", "客户订单", "sales", "saleorder"),
    "purchase": ("采购", "供应商", "purchase", "procurement"),
    "energy": ("能耗", "用能", "用电", "用水", "energy"),
    "inventory": ("仓储", "库存", "库龄", "inventory", "warehouse"),
}

REQUIREMENT_POLICY = (
    "原始 requirement 与 customerRequirement 是业务目标的依据。"
    "不得把行业常见指标、模板预设指标或模型自己补充的内容当成用户明确要求。"
    "用户只说'财务和设备大屏'时，必须保留这两个业务板块，但只选真实数据能够支持的必要指标；"
    "不得自动要求各占50%、近30天、在线率、OEE、利润率、同比或目标完成度。"
    "不得自行增加质检、销售等板块来替换用户要求；订单金额不能冒充已确认收入，记录数不能冒充比率。"
    "用户明确指定的指标、维度、时间和数量仍必须严格满足。"
    "对于未明确列出指标的宽泛业务板块，若确实缺少可用数据，可在该板块用可见的 JText 说明具体缺少的数据或口径，"
    "同时展示其他有依据的结果；缺数据不填0、不当正常、不用无关指标顶替，也不能静默删掉该板块。"
    "若真实字段能够支持用户目标，不得用缺数据说明逃避实现。"
    "宽泛的财务板块也可以由资金支付、收付款、费用等真实财务数据支撑，不强制成本、利润指标。"
    "申请支付金额不等于实际支出；合同累计已付金额不能在多次审批记录之间重复相加，必须准确标明统计口径。"
    "只有附件字段的财务报表不能直接求和成本利润；应继续查找可用的资金/费用业务表。办公用品价值不能冒充财务核心指标。"
    "状态编码的含义只能来自字段注释、已提供的枚举或字典，不能猜测0/1/2分别代表正常/维修/停用。"
    "没有编码释义时保留原始编码并明确标注'状态编码'，不得伪造中文状态。"
    "前N名的饼图不能冒充全量占比，应明确标注TOP N范围或把剩余类别汇总为其他。"
)


def business_scopes(text: str) -> set[str]:
    """从文本中识别业务板块（资金/设备/质量/销售/采购/能耗/库存），返回板块键集合。

    纯关键词匹配（中英文别名表 ``SCOPE_TERMS``），因此只用于"该不该保留这个板块"的范围判断，
    不作为字段级证据。识别不到时返回空集，调用方按"未限定板块"处理。
    """

    lowered = str(text or "").lower()
    return {scope for scope, terms in SCOPE_TERMS.items() if any(term in lowered for term in terms)}


def table_scope_score(table: dict, scope: str) -> int:
    """给"某张表属于某业务板块"打分：命中表名/表注释计 8 分，仅命中字段注释计 1 分。

    财务板块额外按可用金额字段数量加分（每列 8 分，上限 32），而"只有附件字段"的财务命名表
    会被降权到 1/4——避免把附件归档表当成财务数据源。分数只影响选表优先级与上下文预算。
    """

    heading = (str(table.get("tableName") or "") + " " + str(table.get("tableComment") or "")).lower()
    columns = " ".join(str(c.get("columnName") or "") + " " + str(c.get("comment") or "")
                       for c in table.get("columns") or []).lower()
    score = sum(8 if term in heading else 1 if term in columns else 0 for term in SCOPE_TERMS[scope])
    if scope == "finance" and any(term in heading for term in SCOPE_TERMS[scope]):
        measures = financial_measure_columns(table)
        score += min(32, len(measures) * 8)
        if not measures and any(term in columns for term in ("附件", "attachment", "file")):
            score //= 4
    return score


def financial_measure_columns(table: dict) -> list[str]:
    """Prefer usable monetary fields over finance-named attachment archives."""
    result = []
    for column in table.get("columns") or []:
        name = str(column.get("columnName") or "")
        text = (name + " " + str(column.get("comment") or "")).lower()
        numeric = any(t in str(column.get("dataType") or "").lower()
                      for t in ("decimal", "numeric", "float", "double", "int", "real", "money"))
        # Numeric status/unit/category codes are dimensions, not monetary measures.
        category = any(t in text for t in ("类型", "类别", "分类", "状态", "编号", "单位", "币种", "status", "type", "unit", "category"))
        explicit_amount = any(t in text for t in ("金额", "amount", "amt", "余额", "balance"))
        monetary = any(t in text for t in ("金额", "合计", "费用", "余额", "成本", "利润", "amount", "amt", "payment", "pament", "expense", "cost", "balance"))
        if numeric and monetary and (not category or explicit_amount):
            result.append(name)
    return result


def template_business_scopes(template: dict) -> set[str]:
    """判断模板真正绑定的业务板块：只看名称与**固定槽位标题**，不看描述/标签。

    描述与标签常自称"通用/万能"，但槽位标题在绑定阶段不允许被改写，因此它们才是模板实际
    视觉承载的业务范围（例如"累计交易额"槽位意味着财务）。返回板块键集合。
    """
    # Description/tags can claim universal applicability. Inspect actual fixed
    # titles and slots, which the binding stage is not allowed to repurpose.
    texts = [str(template.get("name") or "")]
    for widget in (template.get("content") or {}).get("widgets") or []:
        texts.append(str(widget.get("componentName") or widget.get("title") or ""))
        option = (widget.get("config") or {}).get("option") or {}
        if isinstance(option, dict):
            title = option.get("title")
            texts.append(str(title.get("text") or "") if isinstance(title, dict) else str(title or ""))
            body = option.get("body")
            if isinstance(body, dict):
                texts.append(str(body.get("text") or ""))
    return business_scopes(" ".join(texts))


def compatible_auto_templates(requirement: str, templates: list[dict]) -> list[dict]:
    """自动模式下先过滤候选模板：模板的实际板块必须**覆盖**需求要求的全部板块。

    需求未体现任何板块时原样返回全部候选（不因此收窄选择）；过滤后为空也不在这里兜底，
    由调用方决定是走无模板还是继续让模型选，确保"不匹配就不套模板"。
    """

    scopes = business_scopes(requirement)
    if not scopes:
        return templates
    return [template for template in templates if scopes <= template_business_scopes(template)]
