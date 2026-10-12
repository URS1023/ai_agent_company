"""Complete node inventory and the canonical editable configuration contract.

中文补充：本模块是"节点目录"的唯一来源，也是配置契约与执行器之间的对照表：

- ``NODES``：44 种节点（含迁移中的来源节点）的展示元数据——标题、分组、说明、来源标记。
  任何 ``kind`` 必须出现在这里，否则 ``graph.inspect_graph`` 直接判为"未知节点类型"。
- ``CONFIGS``：可执行节点（``kind -> Pydantic 配置模型``）的映射。**这是唯一契约来源**：
  ``graph.check_config`` 用它校验节点配置，``nodes.execute_node`` 用它反序列化配置，
  ``node_catalog()`` 用它生成前端的 configSchema。三者共用同一份模型，因此
  "目录里能看到的字段"与"执行时能读到的字段"不会漂移。
- ``EDITABLE_CONFIG_DEFAULTS``：人工建节点与 AI 生成草稿共用的默认配置骨架，见下方注释。
- 执行器分支的对应关系：``nodes.execute_node`` 用 ``if node.kind == ...`` 顺序分派，
  新增 kind 必须同时补 CONFIGS、NODES、EDITABLE_CONFIG_DEFAULTS、check_config 的专项校验
  与 execute_node 的分支，否则会出现"能保存但不能执行"或"能执行但无配置校验"的不一致。

重要约束：CONFIGS 中没有的 kind 属于"迁移中节点"，只能作为草稿存在，不能发布或执行。
"""
from copy import deepcopy

from app.branding import PRODUCT_NAME
from app.modules.workflows.schemas import (
    AgentConfig, AggregatorConfig, AlertConfig, AlertNotificationConfig, ArithmeticConfig, ClassifierConfig,
    BusinessReportConfig, BusinessNoticeConfig,
    CodeConfig, DelayConfig, EmptyConfig, ExtractionConfig, ExtractorConfig,
    DocumentExtractorConfig, ListOperationConfig,
    HttpConfig, IteratorConfig, KnowledgeConfig, LlmConfig, LoopConfig,
    ParallelConfig, PredicateConfig, QueryConfig, QuestionConfig, RulesConfig, StartConfig,
    SetConfig, SetVariableConfig, SubflowConfig, TemplateConfig, TextConfig,
    ToolNodeConfig, WhileConfig,
)
from app.modules.writeback.schemas import OutputTableConfig
from app.modules.quality.schemas import DetectorConfig, QualityConfig
from app.modules.workflows.value_schemas import ReadValuesConfig, CalculateValuesConfig, OutputValuesConfig

# kind -> 配置模型。多个 kind 复用同一模型是刻意的：例如 condition/filter 都是布尔表达式，
# database-query/report-analysis 都是"数据源 + 查询 + 参数 + 行数上限"，
# qc-appearance/qc-component 只是检测目标不同。复用表示它们的配置契约必须保持一致。
CONFIGS = {
    "business-report": BusinessReportConfig, "business-notify": BusinessNoticeConfig,
    "read-values": ReadValuesConfig, "calculate-values": CalculateValuesConfig, "output-values": OutputValuesConfig,
    "document-extractor": DocumentExtractorConfig, "list-operator": ListOperationConfig,
    "start": StartConfig, "end": EmptyConfig, "reply": EmptyConfig, "merge": EmptyConfig,
    "http-request": HttpConfig, "database-query": QueryConfig, "report-analysis": QueryConfig,
    "jsonpath-extract": ExtractionConfig, "arithmetic": ArithmeticConfig,
    "condition": PredicateConfig, "filter": PredicateConfig, "set-operation": SetConfig,
    "rule-evaluate": RulesConfig, "alert-evaluate": AlertConfig, "alert-notify": AlertNotificationConfig,
    "output-table": OutputTableConfig,
    "qc-appearance": DetectorConfig, "qc-component": DetectorConfig, "qc-evaluate": QualityConfig,
    "llm": LlmConfig, "agent": AgentConfig, "knowledge": KnowledgeConfig,
    "question": QuestionConfig, "classifier": ClassifierConfig, "extractor": ExtractorConfig,
    "loop": LoopConfig, "iterator": IteratorConfig, "while": WhileConfig,
    "parallel": ParallelConfig, "aggregator": AggregatorConfig, "delay": DelayConfig,
    "subflow": SubflowConfig, "setvar": SetVariableConfig, "text": TextConfig,
    "template": TemplateConfig, "code": CodeConfig, "tool": ToolNodeConfig,
}

# These values are deliberately editable rather than executable.  Required
# resource identifiers stay empty until the user selects an authorized record,
# while collection-shaped fields always have enough structure for the visual
# property editors to render.  The same defaults are returned to manual node
# creation and are applied to AI drafts, so those two paths cannot drift.
#
# 中文补充：默认值只保证"可编辑、可渲染"，不代表可直接运行——资源类标识
# （datasourceId / metricId / ruleId / toolName 等）一律留空，必须由用户显式选择授权记录。
# ``editable_config`` 返回深拷贝，调用方修改不会污染这份全局默认；这里出现的字面量与
# CONFIGS 中模型的必填项、别名必须保持一致，否则新建节点会立刻处于"配置不完整"状态。
EDITABLE_CONFIG_DEFAULTS = {
    "business-report": {"kind": "alert", "title": "业务检查报告"},
    "business-notify": {"audience": "executor"},
    "read-values": {"readMode": "table", "datasourceId": "", "datasourceVersion": None, "tableName": "", "deviceId": None, "fields": [], "filters": [], "maxRows": 10000},
    "calculate-values": {"calculations": [{"name": "计算结果", "operation": "subtract", "left": {"source": "field", "field": ""}, "right": {"source": "constant", "value": 0}}]},
    "output-values": {"fields": [], "conclusion": "normal", "message": ""},
    "document-extractor": {"separator": "\n\n"},
    "list-operator": {"filterEnabled": False, "expression": {"value": True}, "sortEnabled": False, "sortField": "", "sortOrder": "asc", "limitEnabled": False, "limit": 10},
    "start": {"variables": []}, "end": {}, "reply": {}, "merge": {},
    "http-request": {
        "datasourceId": "", "datasourceVersion": None, "method": None, "url": None,
        "headers": {}, "params": {}, "body": None,
        "auth": {"type": "none", "credentialName": None, "headerName": "Authorization"},
        "responseFormat": None, "timeoutSeconds": None, "retry": 0,
        "retryInterval": 500, "confirmWrites": False,
    },
    "database-query": {
        "datasourceId": "", "datasourceVersion": None, "query": "",
        "parameters": {}, "maxRows": 1000,
    },
    "report-analysis": {
        "datasourceId": "", "datasourceVersion": None, "query": "",
        "parameters": {}, "maxRows": 1000,
    },
    "jsonpath-extract": {"recordPath": "$.data", "fields": []},
    "arithmetic": {"fields": [{"name": "", "formula": ""}]},
    "condition": {"expression": {"op": "gte", "args": [{"field": ""}, {"value": 0}]}},
    "filter": {"expression": {"op": "gte", "args": [{"field": ""}, {"value": 0}]}},
    "set-operation": {"operation": "intersection", "keys": []},
    "rule-evaluate": {
        "mode": "alert",
        "rules": [{
            "id": "rule_draft", "name": "", "severity": "warning", "message": "",
            "expression": {"op": "gte", "args": [{"field": ""}, {"value": 0}]},
        }],
    },
    "alert-evaluate": {"ruleId": "", "ruleRevision": 1, "deviceId": None},
    "alert-notify": {
        "audience": "executor", "outcomes": ["issues", "no_data"],
        "minimumSeverity": "warning",
    },
    "output-table": {
        "targetId": "", "targetVersion": 1, "mode": "insert",
        "recordPath": "$.data", "fields": [], "keys": [],
    },
    "qc-appearance": {
        "metricId": "", "metricVersion": 1, "deviceId": "",
        "datasourceId": "", "datasourceVersion": 1, "requestMode": "saved",
        "sampleField": "sampleId", "imageField": "imageFileId",
    },
    "qc-component": {
        "metricId": "", "metricVersion": 1, "deviceId": "",
        "datasourceId": "", "datasourceVersion": 1, "requestMode": "saved",
        "sampleField": "sampleId", "imageField": "imageFileId",
    },
    "qc-evaluate": {
        "templateId": "", "templateRevision": 1, "deviceId": "",
        "sampleId": "", "sampleMode": "fixed",
    },
    "llm": {
        "modelProfile": "reasoning", "systemPrompt": "",
        "promptTemplate": "{{ input }}", "temperature": 0.1,
    },
    "extractor": {
        "modelProfile": "reasoning", "systemPrompt": "",
        "promptTemplate": "{{ input }}", "temperature": 0.1,
        "fields": [{"name": "", "type": "string", "description": "", "required": False}],
    },
    "agent": {"botId": "", "mode": "model", "usePublished": True, "queryTemplate": "{{ input }}"},
    "knowledge": {
        "knowledgeBaseIds": [], "queryTemplate": "{{ input }}",
        "topK": 6, "minScore": 0, "mode": "hybrid",
    },
    "question": {"prompt": "", "answerKey": "answer", "required": True, "fields": []},
    "classifier": {
        "mode": "model", "modelProfile": "reasoning", "systemPrompt": "",
        "promptTemplate": "{{ input }}", "temperature": 0.1,
        "cases": [{
            "id": "case_draft", "label": "", "description": "",
            "expression": {"op": "gte", "args": [{"field": ""}, {"value": 0}]},
        }],
        "fallback": "other",
    },
    "loop": {
        "iterations": 1, "updates": [],
        "keepHistory": False, "subgraph": None,
    },
    "iterator": {"updates": [], "subgraph": None},
    "while": {
        "expression": {"op": "eq", "args": [{"field": ""}, {"value": True}]},
        "updates": [], "maxIterations": 20, "subgraph": None,
    },
    "parallel": {"branchNames": []},
    "aggregator": {"groupBy": [], "metrics": [{"name": "count", "operation": "count", "field": ""}]},
    "delay": {"seconds": 60},
    "subflow": {"workflowId": "", "workflowVersion": 1, "timeoutSeconds": 120, "confirmWrites": False},
    "setvar": {"keepInput": True, "variables": [{"name": "", "path": "$.input"}]},
    "text": {"operation": "stringify", "find": "", "replacement": "", "separator": ","},
    "template": {"template": "{{ input }}"},
    "code": {"code": "result = {}", "timeoutSeconds": 120},
    "tool": {"toolName": "", "arguments": {}, "includeInput": False},
}
# kind -> (标题, 分组, 一句话说明, 来源)。这个字典的键集合是图的合法 kind 全集（含迁移中节点），
# 键顺序决定前端节点面板的展示顺序；``source`` 目前统一为产品名，历史来源（如 dify）会带来
# "可见但不可执行"的节点：NODES 有、CONFIGS 没有，由 node_catalog 的 available 字段表达。
NODES = {
    "business-report": ("整理业务报告", "业务", "汇总可视化节点已判定的结果，展示单据、设备、证据和待确认项，不重新判定规则", PRODUCT_NAME),
    "business-notify": ("业务报告通知", "业务", "保存报告后通知执行人；相同结果不重复通知", PRODUCT_NAME),
    "read-values": ("读取值", "数据", "选择数据表和字段，按真实注释查找并筛选记录", PRODUCT_NAME),
    "calculate-values": ("计算值", "规则", "选择字段计算日期间隔、加减乘除或转换值", PRODUCT_NAME),
    "output-values": ("输出结果", "基础", "选择输出字段、结果和说明", PRODUCT_NAME),
    "document-extractor": ("文档提取器", "数据", "将已上传文档解析为文本", PRODUCT_NAME),
    "list-operator": ("列表操作", "数据", "筛选、排序并限制列表输出数量", PRODUCT_NAME),
    "start": ("开始", "基础", "运行输入", PRODUCT_NAME),
    "end": ("结束", "基础", "保存当前输出", PRODUCT_NAME),
    "reply": ("回复输出", "基础", "输出结果供调用方使用", PRODUCT_NAME),
    "http-request": ("HTTP 采集", "数据", "复用已授权的单条或批量请求", PRODUCT_NAME),
    "database-query": ("数据库查询", "数据", "授权源上的只读 SQL", PRODUCT_NAME),
    "jsonpath-extract": ("字段提取", "数据", "JSONPath 与字段类型选择", PRODUCT_NAME),
    "arithmetic": ("计算字段", "规则", "精确加减乘除及括号", PRODUCT_NAME),
    "filter": ("记录筛选", "规则", "根据条件保留记录", PRODUCT_NAME),
    "condition": ("条件分支", "控制", "AND/OR、比较、范围及真假分支", PRODUCT_NAME),
    "set-operation": ("交并差", "规则", "按完整值或选定字段匹配记录", PRODUCT_NAME),
    "rule-evaluate": ("逐项规则判定", "规则", "异常条件或合格条件，输出逐行问题", PRODUCT_NAME),
    "merge": ("结果汇合", "控制", "汇合实际到达分支，不读取未执行结果", PRODUCT_NAME),
    "report-analysis": ("AI 数据分析", "数据", "调用统一自然语言分析服务", PRODUCT_NAME),
    "alert-evaluate": ("设备预警业务", "业务", "固定设备策略修订，生成可追溯的问题与报告", PRODUCT_NAME),
    "alert-notify": ("预警站内通知", "业务", "按实际报告结果通知执行人或授权工作区成员", PRODUCT_NAME),
    "qc-appearance": ("外观缺陷识别", "业务", "关联设备、图片与视觉服务", PRODUCT_NAME),
    "qc-component": ("成分检测", "业务", "关联检测设备与成分指标", PRODUCT_NAME),
    "qc-evaluate": ("质检业务判定", "业务", "关联模板、检测指标、记录与复核", PRODUCT_NAME),
    "output-table": ("目标表写入", "业务", "写入明确授权的目标表和字段", PRODUCT_NAME),
    "llm": ("大模型", "AI", "模型对话调用", PRODUCT_NAME),
    "agent": ("智能体", "AI", "调用已发布智能体", PRODUCT_NAME),
    "knowledge": ("知识检索", "AI", "检索授权知识库", PRODUCT_NAME),
    "question": ("提问", "AI", "等待用户补充输入", PRODUCT_NAME),
    "classifier": ("问题分类器", "AI", "根据分类说明识别用户问题并选择分支，也可使用明确规则", PRODUCT_NAME),
    "extractor": ("信息抽取", "AI", "结构化信息抽取", PRODUCT_NAME),
    "loop": ("循环", "控制", "显式有界循环子图", PRODUCT_NAME),
    "iterator": ("迭代", "控制", "逐项运行子图", PRODUCT_NAME),
    "while": ("条件循环", "控制", "有界循环与终止条件", PRODUCT_NAME),
    "parallel": ("并行分支", "控制", "将同一输入分发到多个独立分支并在汇合节点收口", PRODUCT_NAME),
    "aggregator": ("聚合", "数据", "字段聚合与分组", PRODUCT_NAME),
    "delay": ("延迟", "控制", "持久化等待与恢复", PRODUCT_NAME),
    "subflow": ("子流程", "控制", "固定版本的工作流复用", PRODUCT_NAME),
    "setvar": ("设置变量", "数据", "显式上下文变量映射", PRODUCT_NAME),
    "text": ("文本处理", "数据", "文本转换", PRODUCT_NAME),
    "template": ("模板渲染", "数据", "有界文本模板", PRODUCT_NAME),
    "code": ("代码", "工具", "受控隔离代码执行", PRODUCT_NAME),
    "tool": ("工具调用", "工具", "统一工具网关与参数校验", PRODUCT_NAME),
}


def editable_config(kind: str) -> dict:
    """Return an isolated, UI-safe configuration skeleton for one node kind.

    中文补充：深拷贝默认骨架，避免调用方（属性编辑器、AI 草稿补齐）就地修改而污染全局默认。
    kind 不在默认表中时返回空字典——迁移中节点没有可编辑属性，不应凭空造字段。
    """
    return deepcopy(EDITABLE_CONFIG_DEFAULTS.get(kind, {}))


def _fill_editable(default, candidate):
    """Fill missing/structurally unusable containers without hiding scalar errors.

    中文补充：递归补齐"容器类"字段（对象缺键、数组类型不符），但**不动标量**：
    标量错误必须原样留给 Pydantic 抛出可读的校验错误，不能被默认值悄悄掩盖。
    数组的处理见下方关于表达式参数数组的注释。
    """
    if isinstance(default, dict):
        supplied = candidate if isinstance(candidate, dict) else {}
        return {
            **supplied,
            **{
                key: _fill_editable(value, supplied[key]) if key in supplied else deepcopy(value)
                for key, value in default.items()
            },
        }
    if isinstance(default, list):
        if not isinstance(candidate, list):
            return deepcopy(default)
        if default and isinstance(default[0], dict):
            # Expression argument arrays are intentionally heterogeneous: one
            # item can be a field reference, another a literal, and another a
            # nested operator. Hydrating every item from the first (usually
            # ``{"field": ""}``) corrupts valid model output into objects
            # that contain both ``field`` and ``value``. Keep expression AST
            # items exactly as supplied; the strict expression validator will
            # still reject unsupported keys or malformed operators.
            #
            # 中文补充：判定依据是"数组首项只含 field/value/op/args"这一组表达式 AST 键，
            # 命中即整数组原样放行；其他对象数组（如 fields、cases）才按首项形状逐项补齐。
            if set(default[0]).issubset({"field", "value", "op", "args"}):
                return candidate
            return [_fill_editable(default[0], item) for item in candidate]
        return candidate
    return candidate


def hydrate_editable_config(kind: str, candidate) -> dict:
    """Normalize model-produced config to the documented top-level properties.

    中文补充：AI 生成草稿的"修复"入口。先用该 kind 的 JSON Schema（by_alias）取顶层属性名，
    丢弃模型自造的额外键，再用 ``_fill_editable`` 补齐缺失结构。只处理顶层键的过滤 +
    结构补齐，不做语义修复；语义仍由 ``graph.check_config`` 严格校验。
    kind 不在 CONFIGS 中时不做任何补齐，直接返回原字典（迁移中节点没有契约可依）。
    """
    if kind not in CONFIGS:
        return candidate if isinstance(candidate, dict) else {}
    schema = CONFIGS[kind].model_json_schema(by_alias=True)
    allowed = set(schema.get("properties") or {})
    supplied = candidate if isinstance(candidate, dict) else {}
    supplied = {key: value for key, value in supplied.items() if key in allowed}
    return _fill_editable(editable_config(kind), supplied)


def node_catalog() -> list[dict]:
    """Return source mapping, actual availability and documented config schemas.

    中文补充：``GET /api/v1/workflows/catalog`` 的数据源，也是 AI 生成提示词里"可用节点目录"的来源。
    ``available`` 为 False 表示该 kind 只在 NODES 里（迁移中），不能发布或执行；
    这种情况下 configSchema 与 defaultConfig 返回 None，前端应禁用该节点的属性编辑。
    """
    return [{"kind": kind, "title": item[0], "group": item[1], "description": item[2], "source": item[3],
             "available": kind in CONFIGS,
             "configSchema": CONFIGS[kind].model_json_schema(by_alias=True) if kind in CONFIGS else None,
             "defaultConfig": editable_config(kind) if kind in CONFIGS else None}
            for kind, item in NODES.items()]
