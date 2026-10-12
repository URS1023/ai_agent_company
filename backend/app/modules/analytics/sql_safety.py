"""AST-based SQL authorization, field qualification and driver parameter binding."""

# 本模块是数据分析唯一的 SQL 授权关口：手工 SQL、自然语言生成的 SQL、报表组件
# SQL 都必须先经过这里的 AST 白名单管线，然后才允许打开数据源连接。
# 管线的顺序即安全约束，不能调换或跳过：
#   1. 语法解析（sqlglot，按 provider 选择方言）——解析失败即拒绝；
#   2. 语句形态：只允许一条 SELECT 或非递归 WITH；
#   3. 节点白名单（FORBIDDEN_NODES 一律拒绝：写入、DDL、事务、锁、变量、管理语句）；
#   4. 函数白名单（SAFE_FUNCTIONS，未登记函数一律拒绝）；
#   5. 表范围：拒绝表函数/文件/外部地址/动态 SQL、拒绝跨库或跨 schema、
#      拒绝未授权表与 sqlite_/pg_/information_schema 系统表，并把表名重新引号化；
#   6. 参数：占位符必须与提交值完全一致，值类型与长度受限；
#   7. 字段范围：用限定化（qualify + expand_stars）校验列、别名与关联关系；
#   8. 行数：只追加/收紧 LIMIT，并以 max_rows + 1 作为探测截断的取值。
#
# 设计意图是 fail-closed：任何解析异常、未知节点、未知函数、无法判定的字段域
# 都直接抛业务错误拒绝执行，绝不降级成“放行执行”或“只记录告警”。
# 关键依赖：sqlglot（解析、qualify、重新生成 SQL）、app.errors.ValidationError。
# 对外契约：parse_read_query() 为纯静态拒绝；authorize_sql() 返回经过引号化与
# 参数校验的 SafeQuery；driver_query() 负责驱动层参数绑定与百分号转义。
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from uuid import uuid4

import sqlglot
from sqlglot import Dialect, exp
from sqlglot.errors import OptimizeError, ParseError, TokenError, UnsupportedError
from sqlglot.optimizer.qualify import qualify

from app.errors import ValidationError


class SqlFieldValidationError(ValidationError):
    """The read-only statement passed policy checks but has invalid field scope."""

    # 表示语句已通过语句类型与节点/函数白名单，但字段、别名或关联关系不在授权范围
    # 内。调用方据此触发一次“同一授权 schema 内纠错重试”，而不是重试数据库连接。


class SqlDisplayFunctionValidationError(ValidationError):
    """Replace character-code display functions with literal text before execution."""


class SqlSyntaxValidationError(ValidationError):
    """Parser rejected grammar before any query execution or authorization."""

    # 语法解析阶段即失败，说明还没有做任何表/字段授权判断，也没有打开数据源；
    # 该错误只允许在拿到完整 schema 的前提下重试，不能据此推断表名或字段。


# provider 到 sqlglot 方言的映射；elasticsearch 的 SQL 接口是 MySQL 兼容子集，
# 因此复用 mysql 方言解析与生成，避免为其单独维护一套语法规则。
DIALECTS = {"mysql": "mysql", "postgres": "postgres", "sqlite": "sqlite", "elasticsearch": "mysql"}
# 允许调用的函数白名单：只保留只读的聚合、字符串、日期、窗口与 JSON 函数。
# 未在此登记的函数（包括用户自定义函数、加解密、文件与系统信息函数）一律拒绝，
# 因此新增函数必须显式评审后加入，不能依赖数据库自身的权限兜底。
SAFE_FUNCTIONS = frozenset("""
ABS AVG SUM COUNT COUNT_IF MIN MAX ROUND TRUNC FLOOR CEIL CEILING POW POWER SQRT SIGN
STDDEV STDDEV_POP STDDEV_SAMP VARIANCE VAR_POP VAR_SAMP MEDIAN PERCENTILE_CONT PERCENTILE_DISC
COALESCE NULLIF IFNULL NVL IF IIF CASE GREATEST LEAST CAST TRY_CAST AND OR
EXISTS
LENGTH CHAR_LENGTH CHARACTER_LENGTH LOWER UPPER TRIM LTRIM RTRIM SUBSTRING SUBSTR
CONCAT CONCAT_WS REPLACE LEFT RIGHT LPAD RPAD POSITION STR_POSITION
NUMBER_TO_STR
DATE DATETIME TIME TIMESTAMP STRFTIME DATE_FORMAT TIME_TO_STR STR_TO_TIME STR_TO_DATE
DATE_ADD DATE_SUB DATE_DIFF DATEDIFF TIMESTAMP_DIFF TS_OR_DS_ADD TS_OR_DS_TO_DATE TS_OR_DS_TO_TIMESTAMP
DATE_TRUNC TIMESTAMP_TRUNC LAST_DAY EXTRACT YEAR MONTH DAY DAY_OF_MONTH DAY_OF_WEEK
DAY_OF_YEAR WEEK WEEK_OF_YEAR HOUR MINUTE SECOND CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP
NOW UNIX_TIMESTAMP FROM_UNIXTIME TIME_TO_UNIX UNIX_TO_TIME
ROW_NUMBER RANK DENSE_RANK PERCENT_RANK CUME_DIST NTILE LEAD LAG FIRST_VALUE LAST_VALUE NTH_VALUE
JSON_EXTRACT JSON_EXTRACT_SCALAR JSON_VALUE JSON_QUERY JSON_ARRAY_LENGTH
JSON_ARRAY JSON_OBJECT JSON_ARRAYAGG JSON_OBJECTAGG
GROUP_CONCAT STRING_AGG ARRAY_AGG ARRAY_SIZE BOOL_AND BOOL_OR
""".split())
# 拒绝的语句/表达式节点：写入（Insert/Update/Delete/Merge/Copy/LoadData/TruncateTable）、
# DDL（Create/Drop/Alter）、管理面（Grant/Revoke/Set/Use/Pragma/Attach/Detach）、
# 事务与锁（Transaction/Commit/Rollback/Lock）、执行与外部源（Execute/Command/Into）、
# 绑定变量与查询提示（Parameter/SessionParameter/PropertyEQ/Hint）。
# 表函数、文件读取、外部地址与动态 SQL 也会命中其中的节点或下文的表名校验。
FORBIDDEN_NODES = frozenset("""
Insert Update Delete Create Drop Alter TruncateTable Merge Command Copy LoadData
Execute Grant Revoke Transaction Commit Rollback Set Use Pragma Attach Detach
Into Lock Parameter SessionParameter PropertyEQ Hint
""".split())


@dataclass(frozen=True)
class SafeQuery:
    """通过授权管线、可以直接交给驱动执行的冻结查询结果。

    不变式：``sql``/``tree`` 是由校验后的 AST 重新生成的语句（表名已按真实大小写
    引号化、``*`` 已展开）；``max_rows`` 是调用方与数据源上限取小后的行数上限；
    ``parameters`` 只包含与占位符一一对应的命名参数值；``tables`` 是本次真实
    使用到的授权表名。冻结（frozen）是为了让执行层无法在绑定后修改范围。
    """

    sql: str
    tree: exp.Expression
    parameters: dict
    max_rows: int
    dialect: str
    tables: tuple[str, ...]


def _schema(tables: list[dict]) -> dict:
    """构造只含“表名 -> 列名”的极简 schema 供 qualify 校验字段域。

    只声明列是否存在、不声明真实类型：字段域校验需要的是“这个列是否被授权”，
    而类型推断交给执行结果，避免把元数据里的类型差异当成授权依据。
    """
    return {table["tableName"]: {
        column["columnName"]: "UNKNOWN" for column in table.get("columns", [])
    } for table in tables if table.get("columns")}


def parse_read_query(sql: str, provider: str) -> exp.Expression:
    """Reject unsafe syntax before metadata discovery can open a data source."""

    # 用途：在打开任何数据库连接之前完成语法与白名单拒绝，属于 fail-closed 的第一道
    # 关卡。参数 sql 为待校验语句，provider 决定解析方言（不支持的 provider 直接
    # 拒绝）。返回已清理节点注释的 AST。
    # 抛出：语句为空/超长/含空字节、语句数不为 1、不是 SELECT 或非递归 WITH、节点数
    # 超限、命中 FORBIDDEN_NODES、递归 CTE、调用未登记函数时抛 ValidationError；
    # 语法无法解析时抛 SqlSyntaxValidationError（更严格，因为此时连表名都无法被安全枚举）。
    if not sql.strip() or len(sql) > 30000 or "\0" in sql:
        raise ValidationError("SQL 不能为空、不能包含空字节，且不能超过 30000 字符")
    dialect = DIALECTS.get(provider)
    if not dialect:
        raise ValidationError("该数据源不支持 SQL 查询")
    try:
        statements = [statement for statement in sqlglot.parse(sql, read=dialect) if statement is not None]
    except (ParseError, TokenError) as exc:
        # 解析失败的默认动作是拒绝：不尝试“尽力执行”或回退到字符串规则匹配。
        raise SqlSyntaxValidationError("SQL 语法无法解析，请检查语句和数据库方言") from exc
    except (UnsupportedError, RecursionError) as exc:
        # 递归过深同样拒绝，避免解析器异常被上层当作可执行语句放行。
        raise ValidationError("SQL 语法无法解析，请检查语句和数据库方言") from exc
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
        raise ValidationError("只允许一条 SELECT 或非递归 WITH 查询")
    tree = statements[0]
    nodes = list(tree.walk())
    if len(nodes) > 3000:
        raise ValidationError("SQL 过于复杂，请拆分查询")
    for node in nodes:
        # 注释必须在白名单校验前清空：避免注释内容影响日志、审计或后续生成。
        node.comments = None
        if type(node).__name__ in FORBIDDEN_NODES:
            raise ValidationError("SQL 含有不允许的写入、变量、锁、执行或管理操作")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            # 递归 CTE 可构造无限展开并绕过行数上限，在线分析明确不允许。
            raise ValidationError("在线分析不允许递归 CTE")
        if isinstance(node, exp.Func):
            function_name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            # SQLGlot's typed JSON constructors use acronym-separated names;
            # keep the policy expressed in database function names.
            function_name = {
                "J_S_O_N_OBJECT": "JSON_OBJECT",
                "J_S_O_N_OBJECT_AGG": "JSON_OBJECTAGG",
            }.get(function_name, function_name)
            if function_name in {"CHR", "CHAR"}:
                raise SqlDisplayFunctionValidationError("分析 SQL 的展示文本请使用字符串字面量，不使用字符编码函数")
            if function_name not in SAFE_FUNCTIONS:
                raise ValidationError(f"分析 SQL 不允许调用函数 {function_name}")
    return tree


def authorize_sql(sql: str, *, provider: str, tables: list[dict], max_rows: int,
                  parameters: dict | None = None, database: str = "") -> SafeQuery:
    """Authorize a finite query and regenerate SQL from its validated AST."""

    # 用途：对语句做表/字段范围授权，并**重新生成** SQL——执行的不是用户或模型给的
    # 原文，而是校验后 AST 的产物，因此原文中的引号技巧、注释、多语句都无法穿透。
    # 参数：tables 是当前数据源已同步并被授权的表与字段快照（唯一授权依据，不接受
    # 客户端另行提交的表名）；database 用于判断表限定符是否跨库；max_rows 与数据源
    # 上限取小；parameters 必须与 SQL 中的 :name 占位符集合完全一致。
    # 返回：SafeQuery，其中 LIMIT 已被收紧为 max_rows + 1（多取一行用于判断截断），
    # 表标识符已按数据库真实大小写引号化。
    # 抛出：跨库/其他 schema、表函数或文件/外部地址/动态 SQL、未授权表、系统表
    # （sqlite_/pg_/information_schema）、超过 32 张表、参数不匹配或类型越界、
    # LIMIT/OFFSET 非字面量时抛 ValidationError；字段、别名或关联关系无效时抛
    # SqlFieldValidationError。副作用：无（不连接数据库、不写库）。
    parameters = parameters or {}
    tree = parse_read_query(sql, provider)
    dialect = DIALECTS[provider]
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    available = {table["tableName"].lower(): table["tableName"] for table in tables if table.get("columns")}
    used_tables = set()
    for table in tree.find_all(exp.Table):
        # 非标识符的表来源（表函数、文件、外部地址、子查询别名式动态 SQL）没有可
        # 校验的授权对象，直接拒绝而不是尝试解释。
        if not isinstance(table.this, exp.Identifier):
            raise ValidationError("不允许通过表函数、文件、外部地址或动态 SQL 读取数据")
        name = table.name
        if table.catalog or (table.db and table.db.lower() not in {"main" if provider == "sqlite" else database.lower()}):
            # 跨库/跨 schema 会绕过表授权清单，因此只允许当前库（SQLite 只允许 main）。
            raise ValidationError("不允许跨数据库或访问其他 schema")
        table.set("db", None)
        if name.lower() in cte_names:
            continue
        if name.lower() not in available or name.lower().startswith(("sqlite_", "pg_", "information_schema")):
            # 系统表前缀即使出现在授权快照中也不放行，避免元数据配置失误导致越权读取。
            raise ValidationError("SQL 引用了不存在或未授权的表", table=name)
        # 用授权清单里的真实表名替换并强制引号化：既修正大小写，也让表名无法被
        # 再次解释为表达式或注入片段。
        table.set("this", exp.to_identifier(available[name.lower()], quoted=True))
        used_tables.add(available[name.lower()])
    if len(used_tables) > 32:
        raise ValidationError("一次查询最多使用 32 张表")
    expected_parameters = set()
    for placeholder in tree.find_all(exp.Placeholder):
        name = placeholder.name
        if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
            raise ValidationError("请使用 :name 格式的命名参数")
        expected_parameters.add(name)
    # 双向比对：SQL 里有而提交值里没有的占位符会留下未绑定参数，多余提交值则可能
    # 暴露调用方意图，两者都拒绝。
    if expected_parameters != set(parameters):
        raise ValidationError("SQL 参数与提交值不一致", required_parameters=sorted(expected_parameters))
    for value in parameters.values():
        # 参数只允许标量，通过驱动绑定传递（不做字符串拼接）；长度与非有限数在
        # 这里拦截，避免驱动层出现类型或精度异常。
        if isinstance(value, str) and len(value) > 10000:
            raise ValidationError("查询参数值过长")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationError("查询参数不能是 NaN 或无穷大")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise ValidationError("SQL 参数只能是字符串、数值、布尔或 null")
    for limit in tree.find_all(exp.Limit):
        # LIMIT 必须是字面量：表达式形式的 LIMIT 无法在执行前确定返回行数上限。
        count = limit.expression
        if not isinstance(count, exp.Literal) or count.is_string or not str(count.this).isdigit():
            raise ValidationError("LIMIT 需要非负整数字面量")
    for offset in tree.find_all(exp.Offset):
        # OFFSET 同样要求字面量，并限制上限以免深分页拖垮源库。
        count = offset.expression
        if not isinstance(count, exp.Literal) or count.is_string or not str(count.this).isdigit() or int(count.this) > 1000000:
            raise ValidationError("OFFSET 必须是 0 到 1000000 之间的整数")
    try:
        # Expanding stars against the restricted schema also enforces field scope.
        # 这一步同时完成三件事：把 * 展开成真实列、校验每个列/别名确实被授权、
        # 生成带引号标识符的规范化 SQL。字段域校验失败即拒绝。
        tree = qualify(tree, dialect=dialect, schema=_schema(tables), validate_qualify_columns=True,
                       expand_stars=True, quote_identifiers=True, identify=True)
    except (OptimizeError, ParseError, UnsupportedError) as exc:
        raise SqlFieldValidationError("SQL 的字段、别名或关联关系无效，或引用了未授权字段") from exc
    request_limit = max(1, min(10000, max_rows))
    existing = tree.args.get("limit")
    # 多取一行用于探测截断：执行层据此区分“恰好等于上限”和“还有更多行”。
    # 若原语句自带更小的 LIMIT，则以原语句为准，避免放宽用户的限定。
    fetch_limit = min(request_limit + 1, int(existing.expression.this)) if existing else request_limit + 1
    tree = tree.limit(fetch_limit, copy=False)
    return SafeQuery(tree.sql(dialect=dialect, comments=False), tree, dict(parameters),
                     request_limit, dialect, tuple(sorted(used_tables)))


def driver_query(query: SafeQuery, provider: str) -> tuple[str, dict | None]:
    """Compile placeholders without replacing text inside SQL string literals."""

    # 用途：把 SQL 中的 :name 占位符编译成驱动认识的参数标记，并把参数值原样交给
    # 驱动绑定。返回 (sql, parameters)；parameters 为 None 表示该语句没有绑定参数。
    # 关键约束：占位符替换必须走 AST 变换（先把 Placeholder 换成一次性随机标记，
    # 再替换标记），不能用字符串 replace——否则字面量里的 :name 或 % 会被误改，
    # 等价于注入。
    if provider == "sqlite":
        return query.tree.sql(dialect="sqlite", comments=False), query.parameters or None
    # PyMySQL uses the ``pyformat`` paramstyle even when the statement has no
    # bind parameters.  A literal DATE_FORMAT pattern such as ``'%Y-%m'`` is
    # therefore interpreted as Python string interpolation unless every
    # percent sign is escaped for the driver.  Previously this happened only
    # when named parameters were present, so otherwise-valid dashboard time
    # series failed before reaching MySQL.
    # 注意：无参语句也必须转义 ``%``，否则 DATE_FORMAT('%Y-%m') 会被驱动当作
    # Python 插值占位符，导致合法报表时间序列在执行前就失败。
    if not query.parameters:
        return query.sql.replace("%", "%%"), None
    # 随机标记保证不会与用户 SQL 里的任何文本冲突；先 AST 变换再替换标记，
    # 只替换真实占位符，字面量内容不受影响。
    marker = "__test5_bind_" + uuid4().hex + "_"
    bound_tree = query.tree.transform(
        lambda node: exp.Var(this=marker + node.name + "__") if isinstance(node, exp.Placeholder) else node,
    )
    sql = bound_tree.sql(dialect=query.dialect, comments=False).replace("%", "%%")
    for name in query.parameters:
        sql = sql.replace(marker + name + "__", "%(" + name + ")s")
    return sql, query.parameters
