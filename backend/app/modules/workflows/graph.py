"""Graph/config validation and current authorization independent of execution.

中文补充：本模块只做两件事——**校验图与配置**、**按当前权限校验图引用到的资源**，
两者都不执行任何节点、不发送任何外部请求（这是"检查"接口能安全暴露给前端的前提）。

- ``inspect_graph``：纯结构 + 配置校验，返回 ``{valid, errors[], order, nodeCount}``。
  它允许草稿带着错误存在（错误逐条定位到节点），因此能同时服务于"保存草稿"与"发布/执行就绪检查"。
- ``authorize_graph``：按图里出现的资源逐个做**当前**授权（不是保存时的授权快照）：
  数据源、数据集/选表、知识库、Bot、子流程、写入目标、工具、质检/预警资源。
  ``active=True`` 额外要求资源处于可用状态（数据源未停用、Bot 已发布等），
  ``execute=True`` 使用执行类权限并要求写库类节点具备对应写回权限。
- ``executable_graph``：二者串联，是"能否立刻运行"的最终判据。

重要约束：撤销数据源权限会连带让旧执行记录/报告不可读，因为读取路径同样调用 ``authorize_graph``；
因此本模块是权限收敛点，不要在别处复制一份更宽松的检查。
"""
from __future__ import annotations

import json
from collections import deque

from pydantic import ValidationError as ContractError

from app.common.owned_records import owned_record
from app.errors import ValidationError
from app.models import DataSource
from app.modules.collection.extraction import compile_path
from app.modules.collection.transport import validate_body_secrets, validate_response_tree
from app.modules.workflows.catalog import CONFIGS, NODES
from app.modules.workflows.expressions import formula_expression, validate_expression
from app.modules.workflows.schemas import FlowGraph


# Pydantic 错误类型 -> 面向用户的说法。缺失键走 "格式不符合节点约束" 兜底，
# 因此这里只需覆盖常见类型；文案会拼进 "节点配置不完整或类型错误：" 之后。
_CONTRACT_ERROR_REASONS = {
    "missing": "缺少必填属性",
    "string_too_short": "不能为空或长度不足",
    "string_too_long": "超过最大长度",
    "greater_than_equal": "小于允许的最小值",
    "less_than_equal": "超过允许的最大值",
    "literal_error": "不在允许值范围内",
    "list_too_short": "至少需要配置一项",
    "list_too_long": "配置项数量超限",
    "extra_forbidden": "不是该节点允许的属性",
}

# 这两条限制刻意使用不同常量名表达不同含义（重试 / 异常处理），值目前相同：
# 这些节点要么是图的端点、要么本身就是分支控制节点，重试或捕获异常会产生歧义。
_RETRY_UNSUPPORTED = {"start", "end", "reply", "question", "delay", "merge", "condition", "classifier", "parallel"}
_ERROR_HANDLE_UNSUPPORTED = {"start", "end", "reply", "question", "delay", "merge", "condition", "classifier", "parallel"}


def _contract_errors(kind: str, exc: ContractError) -> str:
    """Turn Pydantic locations into actionable, documented node requirements.

    中文补充：把 ``loc`` 路径、错误类型与 schema 中该属性的 description 组合成一句话，
    让用户在画布上直接看到"哪个字段、为什么不行、这个字段是什么意思"，而不是原始 pydantic 报错。
    """
    schema = CONFIGS[kind].model_json_schema(by_alias=True)
    properties = schema.get("properties") or {}
    details = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item["loc"])
        root = str(item["loc"][0]) if item["loc"] else "config"
        description = str((properties.get(root) or {}).get("description") or "").strip()
        reason = _CONTRACT_ERROR_REASONS.get(str(item.get("type") or ""), "格式不符合节点约束")
        details.append(f"{location}：{reason}" + (f"（{description}）" if description else ""))
    return "；".join(details)


def validate_json(value, *, maximum: int = 1024 * 1024) -> None:
    """Bound user JSON before preserving it as a workflow graph or run input.

    中文补充：先用 ``validate_response_tree`` 做结构安全校验（深度/键名/原型污染等），
    再序列化确认是有限、可序列化且未超限的 JSON。任何超限都抛 ValidationError，
    不做截断——被静默改小的图或输入会与用户所见不一致。
    """
    validate_response_tree(value)
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError("工作流配置必须是有限、可序列化的 JSON") from exc
    if size > maximum:
        raise ValidationError("工作流配置或结果超出大小限制")


def check_config(node) -> None:
    """按节点类型校验配置契约，并补做模型层难以表达的交叉校验。

    通用部分由 ``CONFIGS[node.kind].model_validate`` 完成；下面的分支处理：
    表达式可验证性（arithmetic/formula、condition/filter、classifier 规则模式、
    loop/iterator/while 的更新公式与子图递归校验、rule-evaluate 的表达式）、
    标识唯一性（计算输出字段、规则项、提取字段、变量名）、JSONPath 可编译性
    （jsonpath-extract、setvar、output-table），以及输出字段名的原型污染防护。

    异常：kind 不在 CONFIGS（迁移中节点）、ContractError、ValidationError、ValueError。
    副作用：无（子图校验通过 ``inspect_graph`` 递归完成，仍然只是校验）。
    """
    if node.kind not in CONFIGS:
        raise ValidationError("此来源节点尚在迁移，不能发布或执行")
    config = CONFIGS[node.kind].model_validate(node.config)
    if node.kind == "arithmetic":
        if len({field.name for field in config.fields}) != len(config.fields):
            raise ValidationError("计算输出字段名重复")
        for field in config.fields:
            if field.name in {"__proto__", "constructor", "prototype"}:
                raise ValidationError("计算输出字段名不安全")
            validate_expression(formula_expression(field.formula) if field.formula is not None else field.expression)
    elif node.kind == "list-operator" and config.filter_enabled:
        validate_expression(config.expression)
    elif node.kind in {"condition", "filter"}:
        validate_expression(config.expression)
    elif node.kind == "classifier" and config.mode == "rules":
        for item in config.cases:
            validate_expression(item.expression)
    elif node.kind in {"loop", "iterator"}:
        for item in config.updates if config.subgraph is None else []:
            validate_expression(formula_expression(item.formula))
        if config.subgraph is not None:
            nested = inspect_graph(config.subgraph)
            if not nested["valid"]:
                raise ValidationError("循环体子图尚不可运行", errors=nested["errors"][:20])
    elif node.kind == "while":
        validate_expression(config.expression)
        for item in config.updates if config.subgraph is None else []:
            validate_expression(formula_expression(item.formula))
        if config.subgraph is not None:
            nested = inspect_graph(config.subgraph)
            if not nested["valid"]:
                raise ValidationError("条件循环体子图尚不可运行", errors=nested["errors"][:20])
    elif node.kind == "rule-evaluate":
        if len({rule.id for rule in config.rules}) != len(config.rules):
            raise ValidationError("规则项标识重复")
        for rule in config.rules:
            validate_expression(rule.expression)
    elif node.kind == "jsonpath-extract":
        compile_path(config.record_path)
        if len({field.name for field in config.fields}) != len(config.fields):
            raise ValidationError("提取字段名重复")
    elif node.kind == "setvar":
        if len({item.name for item in config.variables}) != len(config.variables):
            raise ValidationError("变量名重复")
        for item in config.variables:
            if item.path:
                compile_path(item.path)
    elif node.kind == "output-table":
        compile_path(config.record_path)
        for field in config.fields:
            if field.origin != "literal":
                compile_path(field.path)


def inspect_graph(graph: FlowGraph) -> dict:
    """Keep editable drafts while identifying every publish/execute blocker.

    返回 ``{"valid": bool, "errors": [{"nodeId", "message"}], "order": [...], "nodeCount": int}``。
    ``order`` 是用于执行的拓扑序（Kahn 算法），调用方直接把它交给 ``engine.execute_graph``，
    不要在别处重算，否则校验结果与执行顺序可能不一致。

    校验规则（按代码顺序，全部错误一次性收集，便于逐项修复）：

    1. 规模与序列化：整图 JSON 大小上限 1 MB（``validate_json``），且不得含明文密钥
       （``validate_body_secrets``，凭据只能以 ``${secret.xxx}`` 引用）。
    2. 标识唯一：节点 id、连线 id 不得重复；kind 必须属于 ``NODES``；
       连线两端必须存在；``(source, target, port)`` 三元组不得重复。
    3. 端点：必须且只能有一个 start；至少一个 end 或 reply；start 不能有入线；
       end/reply 不能有出线；其他节点必须有非 ERROR 出线，否则结果无法到达结束节点。
    4. 端口/边语义：
       - ``out``：普通输出，且普通节点**只能**用 out；
       - ``true``/``false``：仅 condition 可用，且必须成对连接（``{true,false}`` 恰好相等）；
       - ``case:<分类编码>``：仅 classifier 可用，必须覆盖全部 cases 与 fallback；
         一旦出现 case 端口就不能同时保留旧的 ``out`` 通用输出；
       - ``error``：仅当来源节点 ``errorStrategy == "fail-branch"`` 且该 kind 支持异常处理时可用，
         并且要求恰好一条 ERROR 连线（fail-branch 必须有、其他策略不能留）。
       - 画布方向字段（sourceSide/targetSide）不参与这里的判断，只影响可视化。
    5. 可达性与环：从 start 出发必须能到达每个非 start 节点；拓扑序长度小于节点数即为
       存在环——不允许无界回路，循环必须用有界的 loop/iterator/while 节点表达。
    6. 输入映射：多上游时必须显式配置 inputs 或使用 merge 节点；inputs 最多 30 项，
       名称非空、≤80 字符、非原型污染名，引用必须来自**已连线的上游**（用祖先集合判断），
       且 path 必须能编译。
    7. 节点内约束：重试与异常处理的 kind 黑名单、以及 ``check_config`` 的配置契约。

    异常：图级别的致命问题（重复标识、未知 kind、悬空连线、重复连线、JSON 非法/超限）直接抛
    ValidationError，因为此时无法把错误归属到具体节点；其余问题以 errors 返回。
    副作用：无。
    """
    validate_json(graph.model_dump(by_alias=True, mode="json"))
    validate_body_secrets(graph.model_dump(by_alias=True, mode="json"))
    nodes = {node.id: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes) or len({edge.id for edge in graph.edges}) != len(graph.edges):
        raise ValidationError("节点或连线标识重复，不能保存有歧义的图")
    if any(node.kind not in NODES for node in graph.nodes):
        raise ValidationError("流程包含未知节点类型，请使用节点目录中的类型")
    if any(edge.source not in nodes or edge.target not in nodes for edge in graph.edges):
        raise ValidationError("连线引用了不存在的节点")
    if len({(edge.source, edge.target, edge.port) for edge in graph.edges}) != len(graph.edges):
        raise ValidationError("存在重复连线")
    errors = []
    def error(message, node_id=None):
        """收集一条校验错误；``node_id`` 用于把问题定位到画布节点，图级问题可为空。"""
        errors.append({"nodeId": node_id, "message": message})
    starts = [node.id for node in graph.nodes if node.kind == "start"]
    if len(starts) != 1:
        error("必须且只能有一个开始节点")
    if not any(node.kind in {"end", "reply"} for node in graph.nodes):
        error("至少需要一个结束或回复输出节点")
    parents, children = {key: [] for key in nodes}, {key: [] for key in nodes}
    for edge in graph.edges:
        parents[edge.target].append(edge.source)
        children[edge.source].append(edge.target)
        source_node = nodes[edge.source]
        # 端口语义按来源节点类型判定；这里逐条收集错误，不做提前返回。
        if edge.port == "error":
            if source_node.error_strategy != "fail-branch" or source_node.kind in _ERROR_HANDLE_UNSUPPORTED:
                error("ERROR 连线只能用于已启用失败分支的节点", edge.source)
        elif source_node.kind == "condition" and edge.port == "out":
            error("条件连线必须选择真或假分支", edge.source)
        elif source_node.kind == "classifier":
            categories = {str(item.get("id")) for item in source_node.config.get("cases", []) if isinstance(item, dict)}
            categories.add(str(source_node.config.get("fallback", "other")))
            if edge.port != "out" and edge.port not in {"case:" + key for key in categories}:
                error("分类连线引用了不存在的分类，请重新连接", edge.source)
        elif source_node.kind != "condition" and edge.port != "out":
            error("普通节点只能使用 out 输出；异常路线请选择 ERROR", edge.source)
    degree = {key: len(value) for key, value in parents.items()}
    queue = deque(key for key, value in degree.items() if not value)
    order, ancestors = [], {key: set() for key in nodes}
    # Kahn 拓扑排序：入度归零才入队。ancestors 顺带记录"能从哪些节点到达"，
    # 供输入映射校验使用（引用必须来自真实上游，而不是任意存在的节点）。
    while queue:
        current = queue.popleft()
        order.append(current)
        for child in children[current]:
            ancestors[child] |= ancestors[current] | {current}
            degree[child] -= 1
            if degree[child] == 0:
                queue.append(child)
    if len(order) != len(nodes):
        error("流程存在循环连线；循环能力应使用专用有界循环节点，不能形成无界回路")
    for node in graph.nodes:
        outgoing = [edge for edge in graph.edges if edge.source == node.id]
        normal_outgoing = [edge for edge in outgoing if edge.port != "error"]
        error_outgoing = [edge for edge in outgoing if edge.port == "error"]
        # 可达性用之前算出的 ancestors：start 必须是每个非 start 节点的祖先。
        if len(starts) == 1 and node.kind != "start" and starts[0] not in ancestors[node.id]:
            error("节点不能从开始节点到达", node.id)
        if node.kind == "start" and parents[node.id]:
            error("开始节点不能有入线", node.id)
        if node.kind in {"end", "reply"} and children[node.id]:
            error("结束节点不能有出线", node.id)
        if node.kind not in {"end", "reply"} and not normal_outgoing:
            error("节点没有后续连线，结果无法到达结束节点", node.id)
        if node.kind == "condition" and {edge.port for edge in graph.edges if edge.source == node.id} != {"true", "false"}:
            error("条件节点需要同时连接真、假分支", node.id)
        if node.kind == "classifier":
            ports = {edge.port for edge in normal_outgoing}
            if any(port.startswith("case:") for port in ports):
                if "out" in ports:
                    error("分类分支不能与旧的通用输出同时连接，请移除通用输出线", node.id)
                required = {"case:" + str(item.get("id")) for item in node.config.get("cases", []) if isinstance(item, dict)}
                required.add("case:" + str(node.config.get("fallback", "other")))
                if not required <= ports:
                    error("请为每个分类和未匹配分类连接后续节点", node.id)
        if node.retry_config.retry_enabled and node.kind in _RETRY_UNSUPPORTED:
            error("当前节点类型不支持失败重试", node.id)
        if node.error_strategy != "none" and node.kind in _ERROR_HANDLE_UNSUPPORTED:
            error("当前节点类型不支持异常处理", node.id)
        if node.error_strategy == "fail-branch" and len(error_outgoing) != 1:
            error("失败分支需要且只能连接一条 ERROR 连线", node.id)
        if node.error_strategy != "fail-branch" and error_outgoing:
            error("未启用失败分支时不能保留 ERROR 连线", node.id)
        if len(parents[node.id]) > 1 and not node.inputs and node.kind != "merge":
            error("多个上游时请明确配置输入映射，或使用结果汇合节点", node.id)
        if len(node.inputs) > 30:
            error("命名输入最多 30 项", node.id)
        for name, reference in node.inputs.items():
            if not name or len(name) > 80 or name in {"__proto__", "constructor", "prototype"}:
                error("输入映射名称无效", node.id)
            if reference.node_id not in ancestors[node.id]:
                error("输入引用必须来自已连线的上游节点", node.id)
            try:
                compile_path(reference.path)
            except ValueError as exc:
                error(str(exc), node.id)
        try:
            check_config(node)
        except ContractError as exc:
            error("节点配置不完整或类型错误：" + _contract_errors(node.kind, exc), node.id)
        except ValidationError as exc:
            error(exc.message, node.id)
        except ValueError as exc:
            error(str(exc), node.id)
    return {"valid": not errors, "errors": errors, "order": order, "nodeCount": len(nodes)}


def graph_sources(graph: FlowGraph) -> set[str]:
    """收集图中引用的所有数据源标识（含子图递归），供授权与运行快照使用。

    只读取 ``datasourceId`` / ``targetDatasourceId`` 两个键；子图若尚未通过契约校验则跳过，
    由外层图的报错负责提示，避免把未校验的嵌套对象带进查询。
    """
    sources = {str(node.config[key]) for node in graph.nodes for key in ("datasourceId", "targetDatasourceId") if node.config.get(key)}
    for node in graph.nodes:
        nested = node.config.get("subgraph")
        if nested:
            try:
                sources |= graph_sources(FlowGraph.model_validate(nested))
            except ContractError:
                # The enclosing graph's contract error remains the actionable
                # draft diagnostic; do not query an unvalidated nested object.
                pass
    return sources


def authorize_graph(ctx, graph: FlowGraph, *, active: bool = False, execute: bool = False) -> None:
    """Source revocation also revokes old graph/output visibility, not just menus.

    中文补充：对图中每个资源引用做"当前"授权检查。它被三条路径复用：
    保存/读取草稿、历史记录与报告读取、执行前校验——因此撤销资源权限后旧结果同样不可读。

    参数：
        active=True：资源必须处于可用状态（数据源 active、Bot 已发布、子流程已发布、
            知识库未停用/未删除、数据源版本与节点固定的版本一致）。
        execute=True：使用执行类权限（如 bots.execute / workflows.execute），
            并要求 output-table 节点在含预警节点时具备 alerts.writeback、
            含质检节点时具备 quality.writeback；tool/code 节点还会实际解析工具是否可执行。

    与版本固定有关的两条关键约束：
    - 节点固定了 datasourceVersion 时，``active`` 下版本不一致直接报错，要求用户重新核对并发布；
    - subflow 节点在 ``active`` 下版本不一致同样报错（``workflowVersion`` 是乐观锁版本，
      运行时会同时固定子流程的发布修订）。

    异常：ValidationError（资源不存在/已停用/版本变化/权限不足）。副作用：仅读库与权限查询，
    不修改任何记录；tool/code 的工具解析使用 ``run_id="workflow-validation"`` 的只读上下文。
    """
    for source_id in graph_sources(graph):
        source = owned_record(ctx, DataSource, source_id, "datasources.view")
        if active and source.status != "active":
            raise ValidationError("流程引用的数据源已停用或移除，不能开始新运行")
    for node in graph.nodes:
        if node.kind == "read-values":
            from app.modules.workflows.value_nodes import read_template
            try:
                selected = CONFIGS[node.kind].model_validate(node.config)
            except ContractError:
                if node.config.get("templateId"):
                    from app.modules.analytics.models import QueryTemplate
                    owned_record(ctx, QueryTemplate, str(node.config["templateId"]), "analytics.view")
            else:
                if selected.read_mode == "dataset":
                    read_template(ctx, selected, active=active)
                elif active:
                    from app.modules.workflows.table_values import selected_table
                    selected_table(ctx, selected)
        if active and node.config.get("datasourceId") and node.config.get("datasourceVersion") is not None:
            from app.modules.analytics.connections import source_version

            selected_source = owned_record(ctx, DataSource, str(node.config["datasourceId"]), "datasources.view")
            if source_version(selected_source) != int(node.config["datasourceVersion"]):
                raise ValidationError("流程固定的数据源版本已变化，请重新核对连接、请求体、字段和 SQL 后发布")
        if node.kind in {"alert-notify", "business-notify"}:
            selected = CONFIGS[node.kind].model_validate(node.config)
            if active and selected.audience == "all":
                from app.common.authorization import require_permission
                require_permission(ctx, "system.operations.manage")
        if node.kind == "knowledge" and node.config.get("knowledgeBaseIds"):
            from sqlalchemy import select
            from app.common.authorization import require_permission, require_resource
            from app.models import KnowledgeBase

            require_permission(ctx, "knowledge.view")
            selected = CONFIGS[node.kind].model_validate(node.config)
            rows = ctx.db.scalars(select(KnowledgeBase).where(
                KnowledgeBase.workspace_id == ctx.workspace_id,
                KnowledgeBase.id.in_(selected.knowledge_base_ids),
                KnowledgeBase.status == "active" if active else KnowledgeBase.status != "deleted",
            )).all()
            if {row.id for row in rows} != set(selected.knowledge_base_ids):
                raise ValidationError("流程引用的知识库不存在、已停用或不属于当前工作区")
            for row in rows:
                require_resource(ctx, "knowledge.view", row)
        if node.kind == "agent" and node.config.get("botId"):
            from app.modules.knowledge_graph.bots import get_bot

            selected = CONFIGS[node.kind].model_validate(node.config)
            bot = get_bot(ctx, selected.bot_id, "bots.execute" if execute else "bots.view")
            if active and selected.use_published and (bot.status != "published" or bot.published_revision is None):
                raise ValidationError("流程引用的 Bot 尚未发布或已停用")
        if node.kind == "subflow" and node.config.get("workflowId"):
            from app.modules.workflows.models import Workflow

            selected = CONFIGS[node.kind].model_validate(node.config)
            child = owned_record(ctx, Workflow, selected.workflow_id,
                                 "workflows.execute" if execute else "workflows.view")
            if active and (child.status != "published" or child.published_revision is None):
                raise ValidationError("子流程尚未发布或已停用")
            # Saved outputs remain readable after a child is edited. Current
            # ownership/permission checks above still apply to historical data.
            if active and child.version != selected.workflow_version:
                raise ValidationError("子流程版本已变化，请重新选择并保存当前版本")
        if node.kind in {"tool", "code"} and execute:
            from app.providers.tool.gateway import ToolGateway, build_context

            # code 节点固定映射到内置沙箱工具 python_execute，且始终禁用网络（allow_network=False）。
            name = "python_execute" if node.kind == "code" else str(node.config.get("toolName") or "")
            execution_context = build_context(
                db=ctx.db, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                run_id="workflow-validation", allow_network=False,
            )
            if ToolGateway(ctx.db).resolve(ctx.workspace_id, name, execution_context=execution_context) is None:
                raise ValidationError("流程引用的工具不存在、未发布或当前账号无权执行")
        if node.kind == "output-table" and node.config.get("targetId"):
            from app.modules.writeback.targets import get_target, validate_node_target
            if not isinstance(node.config["targetId"], str):
                raise ValidationError("写入目标标识必须为文本")
            try:
                selected = CONFIGS[node.kind].model_validate(node.config)
            except ContractError:
                get_target(ctx, node.config["targetId"])
            else:
                validate_node_target(ctx, selected, active=active, execute=execute)
            if execute and any(item.kind == "alert-evaluate" for item in graph.nodes):
                from app.common.authorization import require_permission
                require_permission(ctx, "alerts.writeback")
            if execute and any(item.kind.startswith("qc-") for item in graph.nodes):
                from app.common.authorization import require_permission
                require_permission(ctx, "quality.writeback")
        if node.kind.startswith("qc-"):
            from app.modules.quality.registry import record, validate_inspection
            from app.modules.quality.detection import validate_detector
            try:
                selected = CONFIGS[node.kind].model_validate(node.config)
            except ContractError:
                for key, kind in (("templateId", "templates"), ("metricId", "metrics")):
                    if node.config.get(key):
                        if not isinstance(node.config[key], str):
                            raise ValidationError("质检配置标识必须为文本")
                        record(ctx, kind, node.config[key], deleted=not active)
                if node.config.get("deviceId"):
                    from app.modules.alerts.registry import device
                    if not isinstance(node.config["deviceId"], str):
                        raise ValidationError("质检设备标识必须为文本")
                    device(ctx, node.config["deviceId"], deleted=not active)
            else:
                if node.kind == "qc-evaluate":
                    validate_inspection(ctx, selected, active=active, execute=execute)
                else:
                    validate_detector(ctx, node.kind, selected, active=active, execute=execute)
        if node.kind == "alert-evaluate" and node.config.get("ruleId"):
            from app.modules.alerts.registry import get_record, rule_revision
            if not isinstance(node.config["ruleId"], str):
                raise ValidationError("预警策略标识必须为文本")
            try:
                selected = CONFIGS[node.kind].model_validate(node.config)
            except ContractError:
                # Invalid node configuration may remain an editable draft, but
                # raw JSON must never be passed to a database bound parameter.
                get_record(ctx, "rules", node.config["ruleId"], deleted=not active)
            else:
                revision = rule_revision(ctx, selected.rule_id, selected.rule_revision, active=active, execute=execute)
                if selected.device_id and revision.snapshot["deviceId"] != selected.device_id:
                    raise ValidationError("预警策略与设备工作流绑定的设备不一致")
        nested = node.config.get("subgraph")
        if nested:
            try:
                nested_graph = FlowGraph.model_validate(nested)
            except ContractError:
                continue
            authorize_graph(ctx, nested_graph, active=active, execute=execute)


def executable_graph(ctx, graph: FlowGraph) -> dict:
    """发布/执行前的最终判据：先结构校验，再做 active 状态的资源授权。

    返回 ``inspect_graph`` 的结果（含 ``order``）；任一步失败抛 ValidationError。
    刻意分为两步：结构错误优先返回（用户能立刻定位到节点），只有在图本身可用时才做资源检查。
    """
    validation = inspect_graph(graph)
    if not validation["valid"]:
        raise ValidationError("工作流尚未具备执行条件", errors=validation["errors"])
    authorize_graph(ctx, graph, active=True)
    return validation
