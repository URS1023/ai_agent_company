"""DSH 规划、业务工具透明执行；缺少可验证条件时把具体问题交回同一个 Agent。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
import re

from sqlalchemy import select
from sqlglot import exp

from app.errors import AppError, PermissionErrorEx, ValidationError
from app.models import Run
from app.modules.analytics.connections import database_name, source_version
from app.modules.analytics.models import SourceMetadata
from app.modules.analytics.query_intent import explicit_user_sql, query_context
from app.modules.analytics.queries import execute_query
from app.modules.analytics.schemas import QueryInput
from app.modules.analytics.sql_safety import authorize_sql
from app.modules.runtime.task_control import checkpoint, current


class ScopeValidationError(ValidationError):
    """SQL 尚未执行；DSH 可以依据实际缺失条件修正 SQL 或元数据字段绑定。"""

    code = "dsh_scope_validation"


def is_dsh_query(ctx, tool_context: dict) -> bool:
    """只信任服务端 Worker 上下文或持久运行快照，不接收模型声明的执行身份。"""
    if tool_context.get("execution_owner") == "dsh":
        return True
    run = ctx.db.get(Run, tool_context.get("run_id")) if tool_context.get("run_id") else None
    if run is None:
        return False
    if run.workspace_id != ctx.workspace_id or run.started_by_user_id != ctx.user_id:
        raise PermissionErrorEx("查询运行不属于当前用户或工作区")
    snapshot = run.context_snapshot or {}
    return snapshot.get("chat_engine") == "dsh" or snapshot.get("execution_owner") == "dsh-host"


def _failure(code: str, message: str, **validation) -> dict:
    """结构化失败是规划器的修正输入，不隐藏重规划或请求另一模型。"""
    return {"status": "failed", "error_code": code, "error_summary": message,
            "data": {"validation": validation},
            "metadata": {"execution_owner": "dsh", "planner_sql_replanned": False,
                         "recovery": "correct_input", "business_data_queried": False}}


def _month(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


def _calendar_number(text: str) -> int:
    """解析明确的整数日历数量，不通过相似关键词猜测日期。"""
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if text.startswith("十") and text[1:] in digits:
        return 10 + digits[text[1:]]
    if "十" in text:
        tens, units = text.split("十", 1)
        if tens in digits and (not units or units in digits):
            return digits[tens] * 10 + (digits[units] if units else 0)
    if text in digits:
        return digits[text]
    raise ValueError("不支持的日历数值")


def _periods(text: str, anchor: date) -> list[dict]:
    """识别有唯一日历含义的年月与日期范围；未知时间表达不能猜测后放行。"""
    periods = []
    covered = []
    def add(match, start, end, basis="local_calendar"):
        periods.append({"quote": match.group(0), "start": start, "endExclusive": end, "calendarBasis": basis})
        covered.append(match.span())
    for match in re.finditer(r"(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?\s*(?:至|到|~|～)\s*(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?", text):
        try:
            start = date(*map(int, match.groups()[:3]))
            last = date(*map(int, match.groups()[3:]))
            if last < start:
                raise ValueError()
            add(match, start.isoformat(), (last + timedelta(days=1)).isoformat())
        except ValueError:
            raise ScopeValidationError("原始日期范围无效，请核对日期", reasonCode="invalid_original_date", quote=match.group(0)) from None
    for match in re.finditer(r"(?:最近|过去|近)\s*([-+一二两三四五六七八九十\d.]+)\s*(?:天|日)", text):
        try:
            days = _calendar_number(match.group(1))
        except ValueError:
            days = 0
        if not 1 <= days <= 366:
            raise ScopeValidationError("最近/过去天数需要 1–366 的明确整数；更长范围请使用明确起止日期", reasonCode="relative_day_count_out_of_range",
                                       expected_conditions={"minimumDays": 1, "maximumDays": 366}, quote=match.group(0))
        add(match, (anchor - timedelta(days=days - 1)).isoformat(), (anchor + timedelta(days=1)).isoformat(),
            "local_calendar_days_including_today")
    for match in re.finditer(r"本周|这周|上周|下周", text):
        offset = 7 if match.group(0) == "上周" else -7 if match.group(0) == "下周" else 0
        start = anchor - timedelta(days=anchor.weekday() + offset)
        add(match, start.isoformat(), (start + timedelta(days=7)).isoformat(), "monday_to_next_monday")
    for match in re.finditer(r"本季度|这个季度|上一?个?季度|(?:(\d{4})年|(今年|去年))?第?([一二三四1-4])季度", text):
        token = match.group(0)
        if token in {"本季度", "这个季度"} or token.startswith("上"):
            index = anchor.year * 4 + (anchor.month - 1) // 3 - int(token.startswith("上"))
            year, quarter = index // 4, index % 4 + 1
        else:
            year = int(match.group(1) or anchor.year) - int(match.group(2) == "去年")
            quarter = _calendar_number(match.group(3))
        first_month = (quarter - 1) * 3 + 1
        add(match, _month(year, first_month)[0], _month(year, first_month + 2)[1], "calendar_quarter")
    for match in re.finditer(r"(?:(\d{4})年\s*)?([一二三四五六七八九十]{1,3}|\d+)月|(?<!\d)(\d{4})-(\d{2})(?![-\d])|上个?月|本月|这个月|今年|去年|今天|昨天", text):
        if any(start <= match.start() < end for start, end in covered):
            continue
        token = match.group(0)
        try:
            if token in {"上个月", "上月"}:
                prior = anchor.replace(day=1) - timedelta(days=1)
                bounds = _month(prior.year, prior.month)
            elif token in {"本月", "这个月"}:
                bounds = _month(anchor.year, anchor.month)
            elif token in {"今年", "去年"}:
                year = anchor.year - (token == "去年")
                bounds = (date(year, 1, 1).isoformat(), date(year + 1, 1, 1).isoformat())
            elif token in {"今天", "昨天"}:
                day = anchor - timedelta(days=(token == "昨天"))
                bounds = (day.isoformat(), (day + timedelta(days=1)).isoformat())
            else:
                year = int(match.group(1) or match.group(3) or anchor.year)
                month = _calendar_number(match.group(2) or match.group(4))
                bounds = _month(year, month)
            add(match, *bounds)
        except ValueError:
            raise ScopeValidationError("原始年月无效，请核对日期", reasonCode="invalid_original_date", quote=token) from None
    for match in re.finditer(r"(\d{4})年(?:度)?", text):
        if not any(start <= match.start() < end for start, end in covered):
            year = int(match.group(1))
            if not 1 <= year < 9999:
                raise ScopeValidationError("原始年份超出支持范围", reasonCode="invalid_original_date", quote=match.group(0))
            add(match, date(year, 1, 1).isoformat(), date(year + 1, 1, 1).isoformat())
    remaining = list(text)
    for start, end in covered:
        remaining[start:end] = " " * (end - start)
    unresolved = re.search(r"(?:最近|过去|近)[^，。]{0,20}(?:天|日|周|个月|月|年)|[一二三四五六七八九十]+月|上周|本周|季度|半年|前年|同期|同比|环比|\d{4}[-年]\d{1,2}[-月]\d{1,2}", "".join(remaining))
    if unresolved:
        raise ScopeValidationError("原始时间限定尚不能确定性归一，不能忽略后执行", reasonCode="unsupported_original_period",
                                   quote=unresolved.group(0), correction="将当前取数步骤绑定到用户明确给出的年月或完整起止日期；比较期需有明确日历范围")
    if periods and re.search(r"截至|截止|以来|之前|以前|之后|以后|除外", text):
        raise ScopeValidationError("开放日期边界需要明确起止口径，不能把整段历史缩成某一个月", reasonCode="unsupported_open_date_range",
                                   correction="使用明确起止日期的原始子句，不能忽略截至/以来/之前/之后等限定")
    return periods


def _departments(text: str, periods=()) -> list[dict]:
    """只读取明确写出的部门名称，不把业务领域“财务”推定为部门条件。"""
    result = []
    for period in periods:
        # 去掉已解析的完整日期片段；不能按“天/月/日”单字截断“天津”“日照”等部门名称。
        text = text.replace(period["quote"], " ")
    # 分组维度不限定某个部门；先切断这些短语，避免贪婪匹配把前面的整段指标当作部门名。
    # 仅处理紧邻“部门”的通用量词和介词，“按设备部门”等明确名称仍进入条件校验。
    text = re.sub(r"(?:按照|依照|根据|按|以|全部|所有|各个|每个|各|每)\s*部门(?!\s*[：:=])", "，", text)
    pattern = r"(?:部门[：:=\s]+[‘\"“']?([\w\u4e00-\u9fff]{1,30})|([\u4e00-\u9fffA-Za-z0-9_]{1,30})部门(?!\s*[：:=]))"
    for match in re.finditer(pattern, text):
        name = match.group(1) or match.group(2)
        if match.group(1):
            name = name.split("的", 1)[0]
        name = re.sub(r"^(?:请|帮我|查询|查看|统计|列出|分析|本月|上个月|上月|本次|只看|仅看|改成|改为|换成|调整为)+", "", name)
        # 前置年月、数量或其它限定不能被合并为一个猜测的部门名称。
        name = name.split("的")[-1]
        if name in {"全部", "所有", "各", "各个"}:
            continue
        if not name or re.search(r"除|不含|排除|以外|非|和|与|、", name) or text[:match.start()].endswith("、"):
            raise ScopeValidationError("部门限定无法按单个等值条件验证", reasonCode="unsupported_department_scope", quote=match.group(0),
                                       correction="scope.department 可明确绑定原话中的部门名称字段；排除、多部门与ID映射需使用已核实口径")
        result.append({"quote": match.group(0), "value": name})
    return result


def required_scope(intent: dict, scope: dict) -> dict:
    """单一范围直接继承原话；复合任务多个范围需要引用包含完整限定的原始子句。"""
    texts = [turn["request"] for turn in intent.get("previousUserTurns") or []] + [intent["currentRequest"]]
    original = "\n".join(texts)
    quote = str(scope.get("request_quote") or "")
    if quote and quote not in original:
        raise ScopeValidationError("scope.request_quote 必须逐字来自当前原始请求或已确认追问上下文", reasonCode="scope_quote_not_grounded")
    anchor = date.fromisoformat(intent["referenceDate"])
    periods, departments, period_error = [], [], None
    for text in texts:
        changed = bool(re.search(r"改成|改为|换成|改到|调整为", text))
        try:
            new_periods = _periods(text, anchor)
        except ScopeValidationError as exc:
            period_error = exc
            new_periods = []
        if new_periods:
            periods = new_periods if changed else [*periods, *new_periods]
            if changed:
                period_error = None
        new_departments = _departments(text, new_periods)
        if new_departments:
            departments = new_departments if changed else [*departments, *new_departments]
    if period_error:
        raise period_error
    def unique(items, key, quoted):
        values = {key(item): item for item in items}
        if len(values) > 1:
            candidates = {key(item): item for item in quoted}
            if not quote or len(candidates) != 1 or not set(candidates).issubset(values):
                raise ScopeValidationError("复合任务包含多个业务范围，请明确当前取数步骤对应的原始子句", reasonCode="ambiguous_subtask_scope",
                                           expected_scope={"request_quote": "逐字引用包含当前步骤完整日期和部门限定的原话"}, candidates=list(values.values()))
            return next(iter(candidates.values()))
        return next(iter(values.values()), None)
    return {"period": unique(periods, lambda item: (item["start"], item["endExclusive"]), _periods(quote, anchor) if quote else []),
            "department": unique(departments, lambda item: item["value"], _departments(quote, _periods(quote, anchor)) if quote else []),
            "referenceDate": intent["referenceDate"], "requestQuote": quote}


def _conjuncts(node):
    if isinstance(node, exp.Paren):
        return _conjuncts(node.this)
    if isinstance(node, exp.And):
        return [*_conjuncts(node.this), *_conjuncts(node.expression)]
    return [node]


def validate_scope(safe_query, tables: list[dict], intent: dict, scope: dict) -> dict:
    """验证授权 AST 中真正约束结果的 WHERE 条件，不按 SQL 文本中的关键词判断通过。"""
    required = required_scope(intent, scope)
    if not required["period"] and not required["department"]:
        return {**required, "status": "no_explicit_date_or_department"}
    tree = safe_query.tree
    if not isinstance(tree, exp.Select) or len(list(tree.find_all(exp.Select))) != 1:
        raise ScopeValidationError("当前 SQL 形态无法证明原始日期和部门约束作用于全部结果，请拆成单个 SELECT 取数步骤", reasonCode="unsupported_scope_shape", expected_scope=required)
    table_map = {table["tableName"].lower(): table for table in tables}
    aliases = {table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)}
    if len(set(aliases.values())) != len(aliases):
        raise ScopeValidationError("同一表多个别名的范围关系尚无法核实，请明确拆分当前取数步骤", reasonCode="ambiguous_scope_alias", expected_scope=required)
    where = tree.args.get("where")
    predicates = _conjuncts(where.this) if where is not None else []
    conditions = []
    for predicate in predicates:
        if not isinstance(predicate, (exp.EQ, exp.GTE, exp.GT, exp.LT, exp.LTE)):
            continue
        left, right = predicate.this, predicate.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Literal):
            continue
        table_name = aliases.get(left.table.lower())
        table = table_map.get(table_name or "", {})
        column = next((column for column in table.get("columns") or [] if column["columnName"].lower() == left.name.lower()), None)
        if column:
            conditions.append((table_name, column, type(predicate), str(right.this)))
    def bound(kind, condition):
        binding = scope.get(kind) or {}
        return (not binding or (condition[0] == str(binding.get("table") or "").lower()
                and condition[1]["columnName"].lower() == str(binding.get("column") or "").lower()))
    period = required["period"]
    if period:
        dates = [condition for condition in conditions if bound("time", condition)
                 and re.match(r"^(DATE|DATETIME|TIMESTAMP)\b", str(condition[1].get("dataType") or ""), re.I)]
        starts = {(table, column["columnName"].lower()) for table, column, operator, value in dates
                  if operator is exp.GTE and value in {period["start"], period["start"] + " 00:00:00"}}
        ends = {(table, column["columnName"].lower()) for table, column, operator, value in dates
                if operator is exp.LT and value in {period["endExclusive"], period["endExclusive"] + " 00:00:00"}}
        if not starts.intersection(ends):
            raise ScopeValidationError("SQL 未保留原始请求的完整日期区间；请在同一个真实日期列上使用 >= 起日 AND < 结束边界", reasonCode="date_scope_missing_or_changed",
                expected_scope=required, expected_conditions={"startInclusive": period["start"], "endExclusive": period["endExclusive"]},
                binding_format={"scope": {"time": {"table": "schema真实表名", "column": "schema真实日期列"}}})
    department = required["department"]
    if department:
        def department_name_column(table_name, column):
            name = re.sub(r"^c_|[^a-z]", "", column["columnName"].lower())
            comment = str(column.get("comment") or "").strip()
            return name in {"department", "departmentname", "dept", "deptname"} or comment in {"部门", "部门名称", "所属部门", "归属部门"}
        matches = [condition for condition in conditions if bound("department", condition)
                   and condition[2] is exp.EQ and condition[3] == department["value"]
                   and department_name_column(condition[0], condition[1])]
        if not matches:
            raise ScopeValidationError("SQL 未保留原始部门名称的等值筛选；部门 ID 不能在缺少映射证据时替代名称", reasonCode="department_scope_missing_or_changed",
                expected_scope=required, expected_conditions={"departmentName": department["value"]},
                binding_format={"scope": {"department": {"table": "schema真实表名", "column": "部门名称列"}}})
    return {**required, "status": "verified"}


def execute_dsh_query(ctx, tool_context: dict, source, args: dict) -> dict:
    """DSH SQL 只做权限、AST 与口径校验；成功快照仍使用现有统一查询与交付管线。"""
    checkpoint()
    mode = str(args.get("mode") or "natural_language")
    if mode != "sql":
        return _failure("dsh_explicit_query_required", "DSH 对话由当前 Agent 规划查询；请先读取 schema，再使用 sql 模式提交明确 SQL，不在业务工具中再次调用模型。",
                        requestedMode=mode, supportedModes=["schema", "sql", "profile"], required={"mode": "sql", "task": "当前取数子任务", "query": "基于授权schema的明确只读SQL"})
    try:
        task = str(args.get("task") or "").strip()
        intent = query_context(ctx, tool_context, source.id, task)
        if not intent:
            return _failure("dsh_user_scope_required", "查询缺少服务端保存的原始用户范围，不能执行或自行改写查询。")
        explicit = explicit_user_sql(intent["currentRequest"])
        if not task and explicit is None:
            return _failure("dsh_query_task_required", "请提供 task 描述本次取数步骤；完整用户要求由服务端保留，不应让 SQL 字符串替代业务子任务。", required={"task": "例如：查询指定月份设备部门明细"})
        intent = {**intent, "plannerTask": task or "执行用户明确提供的 SQL", "executionOwner": "dsh"}
        from app.modules.analytics.query_authority import QueryAuthority
        authority = QueryAuthority(ctx, source, run_id=intent["runId"])
        # 当前入口固定为该内置工具；缓存返回也遵守操作台停用开关。
        authority.tool_name = "query_business_data"
        authority.check()
        record = ctx.db.scalar(select(SourceMetadata).where(SourceMetadata.workspace_id == ctx.workspace_id, SourceMetadata.datasource_id == source.id))
        if not record or record.status != "ready" or record.source_version != source_version(source):
            return _failure("dsh_schema_required", "当前版本授权元数据尚未就绪，不能猜测表字段或自动重新同步。", requiredMode="schema")
        authority.bind_schema(record.tables_json)
        sql = explicit if explicit is not None else str(args.get("query") or "")
        max_rows = int(args.get("max_rows") or 100)
        safe = authorize_sql(sql, provider=source.provider, tables=record.tables_json,
                             database=database_name(source),
                             max_rows=min(max_rows, int(source.connection_config.get("maxRows") or 1000)))
        validation = {"status": "explicit_user_sql"} if explicit is not None else validate_scope(safe, record.tables_json, intent, args.get("scope") or {})
        metadata = {"execution_owner": "dsh", "requested_mode": "sql", "executed_mode": "sql", "planner_sql_replanned": False,
                    "query_purpose": "business_result", "user_scope_applied": True, "scope_validation": validation,
                    "guidance": "SQL由当前DSH Agent明确提交，工具未调用模型重写；实际执行SQL见generatedSql。零行不表示财务金额为零；覆盖疑问使用profile，不能反复扩大原始业务范围。"}
        control = current.get()
        cache_key = ("dsh_explicit_sql", ctx.workspace_id, ctx.user_id, intent["runId"], source.id, source_version(source),
                     intent["currentRequest"], json.dumps(intent.get("previousUserTurns") or [], ensure_ascii=False, sort_keys=True),
                     intent["plannerTask"], safe.sql, max_rows)
        cached = control.query_receipts.get(cache_key) if control else None
        if cached:
            checkpoint()
            authority.check_result(safe.sql, cached)
            return {"status": "success", "data": deepcopy(cached), "metadata": {**metadata, "same_turn_result_reused": True}}
        result = execute_query(ctx, QueryInput(datasourceId=source.id, query=sql, mode="sql", sessionId=intent["conversationId"],
            maxRows=max_rows, needAnalysis=False, needChart=False), user_intent=intent, authority=authority)
        data = result.model_dump(mode="json")
        # SQL 完成与本轮工具收口之间也可能收到停止信号，不能继续增加成功回执。
        checkpoint()
        if control and not data.get("truncated") and len(control.query_receipts) < 16 and len(json.dumps(data, ensure_ascii=False)) <= 100000:
            control.query_receipts[cache_key] = deepcopy(data)
        return {"status": "success", "data": data, "metadata": metadata}
    except AppError as exc:
        if exc.extra.get("failure_domain") == "datasource":
            raise
        return _failure(exc.code, exc.message, **exc.extra)
