"""Resilient, traced AI workflow draft generation with strict contracts.

中文补充：``POST /api/v1/workflows/generate`` 的实现——把业务目标交给模型生成一份
**可编辑草稿**，绝不自动保存、发布、运行，也不臆造资源标识。

流水线（每一步都有明确的重试/降级语义）：

1. ``_resource_catalog``：只把用户在生成界面**明确勾选**且当前有权读取的数据源目录交给模型
   （未勾选则不提供任何目录，模型只能把 datasourceId 留空）。
2. 系统提示词是硬契约（见 ``generate``）：只返回一个 JSON、必须恰好一个 start、
   至少一个 end/reply、终止节点不能有出线、config 必须严格遵循节点目录 schema。
3. ``_json_object``：容忍模型用散文或 ``` 包裹的 JSON，逐个 ``{`` 位置尝试 raw_decode。
4. ``_normalize_generated_graph``：修复常见缺陷——把 config 补齐到目录契约
   （``hydrate_editable_config``）、规范化/去重节点与连线标识、把不可用的端口降级为 out、
   展开重叠的卡片、把"没有数据源却接在上游之后的 report-analysis"改写成 llm，
   去掉"end 后又接 reply"这类终止节点出线。
5. ``_bind_selected_resources``：用真实目录覆盖模型自造的数据源 id/版本，找不到就写空。
6. ``_require_editable_structure``：只重试**结构**错误；"节点配置不完整或类型错误"属于
   允许保留的可编辑占位，不触发重试（否则每个未选资源的节点都会被当作模型失败）。
7. 最多 ``MAX_GENERATION_ATTEMPTS``（2）次；第二次会带上上次输出与校验摘要要求完整重写。
   两次都失败时用 ``_fallback_graph`` 生成确定性的安全草稿并标记 mode=fallback，
   避免提供方故障或模型胡说直接毁掉用户的目标描述。

调用方注意：返回的是草稿数据（含 validation 明细与 warnings），不会落库。
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import re
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.common.authorization import require_permission
from app.common.owned_records import owned_record
from app.errors import AppError, ModelError, ValidationError
from app.ids import new_id
from app.models import DataSource, ModelCall
from app.modules.analytics.connections import source_version
from app.modules.runtime import service as runtime
from app.modules.system.service_helpers import commit_change
from app.modules.workflows.catalog import hydrate_editable_config, node_catalog
from app.modules.workflows.graph import authorize_graph, inspect_graph
from app.modules.workflows.schemas import FlowGraph, WorkflowGenerateInput
from app.providers import model

MAX_GENERATION_ATTEMPTS = 2
_NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
# 可用于 database-query / report-analysis 的数据源类型；http 只能给 http-request 用。
_SQL_PROVIDERS = {"mysql", "postgres", "sqlite"}


def _json_object(content: str) -> dict:
    """Accept a JSON object even when a provider wraps it in prose or a fence.

    中文补充：先剥掉 ```json 围栏，再从每个 ``{`` 起点尝试 ``raw_decode``，
    返回第一个能解析成对象的片段。全部失败抛 ModelError（由调用方决定是否重试）。
    """
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    decoder = json.JSONDecoder()
    starts = [0] if candidate.startswith("{") else []
    starts.extend(match.start() for match in re.finditer(r"\{", candidate) if match.start() not in starts)
    for start in starts:
        try:
            value, _ = decoder.raw_decode(candidate[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ModelError("模型没有返回合法的工作流 JSON")


def _coordinate(value: Any, fallback: float) -> float:
    """把模型给的坐标转成画布可用值：非法/非有限/越界（±20000）一律回退到默认坐标。

    回退而不是报错，因为坐标只影响可视化，不该让一份业务上可用的图因坐标而失败。
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if math.isfinite(parsed) and -20000 <= parsed <= 20000 else fallback


def _readable_generated_layout(graph: FlowGraph) -> FlowGraph:
    """Expand overlapping AI cards so their handles and wires remain visible.

    中文补充：只在检测到卡片重叠（横向 <248 或纵向 <112）时才重排：按入度做分层，
    同层按原有 x/y 顺序排列，逐层放到 ``x = 80 + 层号*320``、``y = 120 + 行号*190`` 的网格上，
    并把所有连线方向统一为"右出左进"。孤立/环内节点排到最右侧单独成列。
    它只改 position 与连线方向（纯可视字段），不改节点类型、配置或拓扑。
    """
    dense = any(
        abs(left.position.x - right.position.x) < 248
        and abs(left.position.y - right.position.y) < 112
        for index, left in enumerate(graph.nodes)
        for right in graph.nodes[index + 1:]
    )
    if not dense:
        return graph

    nodes = {node.id: node for node in graph.nodes}
    incoming = {node.id: 0 for node in graph.nodes}
    outgoing = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.source not in nodes or edge.target not in nodes:
            continue
        incoming[edge.target] += 1
        outgoing[edge.source].append(edge.target)
    order_index = {node.id: index for index, node in enumerate(graph.nodes)}
    queue = sorted(
        (node.id for node in graph.nodes if incoming[node.id] == 0),
        key=lambda item: (nodes[item].position.x, nodes[item].position.y, order_index[item]),
    )
    levels = {node.id: 0 for node in graph.nodes}
    visited = set()
    cursor = 0
    while cursor < len(queue):
        source = queue[cursor]
        cursor += 1
        visited.add(source)
        for target in outgoing[source]:
            levels[target] = max(levels[target], levels[source] + 1)
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    fallback_level = max(levels.values(), default=0) + 1
    for node in graph.nodes:
        if node.id not in visited:
            levels[node.id] = fallback_level
            fallback_level += 1

    rows: dict[int, int] = {}
    value = graph.model_dump(mode="json", by_alias=True)
    for item in value["nodes"]:
        column = levels[item["id"]]
        row = rows.get(column, 0)
        rows[column] = row + 1
        item["position"] = {"x": 80 + column * 320, "y": 120 + row * 190}
    for edge in value["edges"]:
        edge["sourceSide"] = "right"
        edge["targetSide"] = "left"
    return FlowGraph.model_validate(value)


def _safe_error(exc: Exception) -> str:
    """Persist an actionable class/reason without leaking provider internals.

    业务异常保留其 message；Pydantic 校验错误只列出最多 8 个出错字段路径
    （用户能据此去补齐配置，但不回显模型原始输出）；其他异常只记类名。
    截断到 500 字符后写入运行记录与重试提示词。
    """
    if isinstance(exc, AppError):
        return f"{type(exc).__name__}: {exc.message}"[:500]
    if isinstance(exc, PydanticValidationError):
        fields = [".".join(str(part) for part in item.get("loc", ())) for item in exc.errors()[:8]]
        return f"ValidationError: 模型图缺少或误填字段（{', '.join(fields)}）"[:500]
    return type(exc).__name__


def _resource_catalog(ctx, resource_ids: list[str]) -> list[dict]:
    """Resolve only resources explicitly selected in the generation dialog.

    中文补充：未勾选任何数据源时返回空列表——模型因此看不到任何资源，只能把 datasourceId 留空。
    每个资源按提供方给出不同摘要：http 给 method/recordPath/allowAutomatic 与字段注释（最多 100 个），
    其余给库名/允许的表/业务域/元数据同步状态（表最多 200 个、业务域 50 个）。
    只包含目录信息，**不含凭据、连接串或 SQL**。
    异常：ValidationError（数据源已移除）；权限不足由 owned_record 抛权限异常。
    """
    if not resource_ids:
        return []
    require_permission(ctx, "datasources.view")
    result = []
    for resource_id in resource_ids:
        row = owned_record(ctx, DataSource, resource_id, "datasources.view")
        if row.status == "deleted":
            raise ValidationError("所选数据源已移除，请重新选择")
        config = row.connection_config or {}
        description = str(config.get("description") or config.get("autoDescription") or "")[:2000]
        item = {
            "id": row.id,
            "name": row.name,
            "type": row.provider,
            "version": source_version(row),
            "enabled": row.status == "active",
            "description": description,
        }
        if row.provider == "http":
            item.update({
                "method": str(config.get("method") or "GET"),
                "recordPath": str(config.get("recordPath") or "$"),
                "allowAutomatic": bool(config.get("allowAutomatic")),
                "fields": [
                    {
                        "name": str(field.get("name") or ""),
                        "path": str(field.get("path") or "$"),
                        "comment": str(field.get("comment") or "")[:500],
                    }
                    for field in (config.get("fields") or [])[:100]
                    if isinstance(field, dict)
                ],
            })
        else:
            item.update({
                "databaseName": str(config.get("databaseName") or config.get("database") or ""),
                "allowedTables": list(config.get("allowedTables") or [])[:200],
                "businessDomains": list(config.get("businessDomains") or [])[:50],
                "metadataStatus": str(config.get("metadataStatus") or "not_synced"),
            })
        result.append(item)
    return result


def _selected_resource(node: dict, prompt: str, resources: list[dict]) -> dict | None:
    """为节点挑选唯一匹配的数据源；不确定时返回 None（宁可不绑定也不猜错）。

    匹配顺序：节点已填且存在于兼容目录中的 id -> 兼容目录里唯一的候选 ->
    节点标题/查询文本与用户提示词中恰好出现一个候选名。``report-analysis`` 接受所有非 http 类型。
    返回 None 时调用方会把 datasourceId 清空，让用户在画布上自己选。
    """
    kind = str(node.get("kind") or "")
    if kind == "http-request":
        compatible = [item for item in resources if item["type"] == "http"]
    elif kind == "database-query":
        compatible = [item for item in resources if item["type"] in _SQL_PROVIDERS]
    elif kind == "report-analysis":
        compatible = [item for item in resources if item["type"] != "http"]
    else:
        return None
    current = str((node.get("config") or {}).get("datasourceId") or "")
    for item in compatible:
        if item["id"] == current:
            return item
    if len(compatible) == 1:
        return compatible[0]
    haystack = " ".join([
        str(node.get("title") or ""),
        str((node.get("config") or {}).get("query") or ""),
        prompt,
    ]).casefold()
    matches = [item for item in compatible if item["name"].strip() and item["name"].casefold() in haystack]
    return matches[0] if len(matches) == 1 else None


def _bind_selected_resources(graph: FlowGraph, prompt: str, resources: list[dict]) -> FlowGraph:
    """Pin exact selected resource IDs/versions and erase model-invented IDs.

    中文补充：只重写 http-request / database-query / report-analysis 三类节点的
    ``datasourceId`` 与 ``datasourceVersion``：匹配到就写入真实 id 与当前版本，
    匹配不到就写空字符串/None。即使模型编造了一个看起来合法的 id，也绝不会被保留下来。
    """
    dumped = graph.model_dump(mode="json", by_alias=True)
    nodes = []
    for value in dumped["nodes"]:
        if value["kind"] not in {"http-request", "database-query", "report-analysis"}:
            nodes.append(value)
            continue
        selected = _selected_resource(value, prompt, resources)
        value["config"]["datasourceId"] = selected["id"] if selected else ""
        value["config"]["datasourceVersion"] = selected["version"] if selected else None
        nodes.append(value)
    return FlowGraph.model_validate({"nodes": nodes, "edges": dumped["edges"]})


def _normalize_generated_graph(candidate: Any, *, prompt: str = "", resources: list[dict] | None = None) -> FlowGraph:
    """Hydrate editable properties and repair common provider formatting defects.

    中文补充：模型输出的"修复 + 契约化"总入口，返回通过 FlowGraph 校验的图。处理内容：

    1. 节点：kind 必须属于可用节点目录（否则 ModelError）；重复 id 直接判错（无法安全 rename 有引用关系的节点）；
       非法 id 用 ``node_<序号>`` 兜底并记录映射，供 inputs/edges 同步改写；
       title 缺失用目录标题兜底；config 走 ``hydrate_editable_config`` 补齐可编辑属性。
    2. inputs：把引用重写成重命名后的真实节点 id，path 默认 ``$``；非对象的输入项直接丢弃。
    3. edges：id 去重、端口只保留 out/true/false（模型常编造 case:*/error），两端重写为真实 id。
       注意：产物是否合法仍由 ``inspect_graph`` 判定，这里只做修复不做承诺。
    4. report-analysis 纠偏：没有数据源却又接在非 start 上游之后时，说明模型想"分析上游数据"，
       而该节点语义是"主动查询数据源"，因此改写成等价的 llm 节点（保留目标描述进提示词）。
    5. 终止节点去重：end 与 reply 都是终止节点，模型常串联成链，造成终止节点有出线的非法图；
       只删除"入度为 1 且自身无出线"的冗余终止节点，不动其他结构。
    6. 最后依次做可读性重排与资源绑定。
    """
    value = candidate if isinstance(candidate, dict) else {}
    raw_nodes = value.get("nodes") if isinstance(value.get("nodes"), list) else []
    if not raw_nodes:
        raise ModelError("模型没有生成工作流节点")
    catalog = {item["kind"]: item for item in node_catalog() if item["available"]}
    ids: dict[str, str] = {}
    seen: set[str] = set()
    prepared: list[dict] = []
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise ModelError("模型节点格式错误")
        kind = str(raw.get("kind") or "")
        if kind not in catalog:
            raise ModelError(f"模型使用了不存在的节点类型：{kind or '空'}")
        original_id = str(raw.get("id") or "")
        if original_id and original_id in ids:
            raise ModelError("模型生成了重复的节点标识")
        node_id = original_id if _NODE_ID.fullmatch(original_id) else f"node_{index + 1}"
        while node_id in seen:
            node_id += "_next"
        seen.add(node_id)
        if original_id:
            ids[original_id] = node_id
        raw_position = raw.get("position") if isinstance(raw.get("position"), dict) else {}
        prepared.append({
            "id": node_id,
            "kind": kind,
            "title": str(raw.get("title") or catalog[kind]["title"]).strip()[:160] or catalog[kind]["title"],
            "position": {
                "x": _coordinate(raw_position.get("x"), 40 + index * 320),
                "y": _coordinate(raw_position.get("y"), 50),
            },
            "inputs": raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {},
            "config": hydrate_editable_config(kind, raw.get("config")),
        })

    for node in prepared:
        mapped_inputs = {}
        for name, reference in node["inputs"].items():
            if not isinstance(name, str) or not isinstance(reference, dict):
                continue
            reference_id = str(reference.get("nodeId") or reference.get("node_id") or "")
            mapped_inputs[name] = {
                "nodeId": ids.get(reference_id, reference_id),
                "path": str(reference.get("path") or "$"),
            }
        node["inputs"] = mapped_inputs

    raw_edges = value.get("edges") if isinstance(value.get("edges"), list) else []
    edges = []
    edge_ids: set[str] = set()
    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, dict):
            continue
        edge_id = str(raw.get("id") or f"edge_{index + 1}")[:100]
        while edge_id in edge_ids:
            edge_id = (edge_id[:91] + f"_{index + 1}")[:100]
        edge_ids.add(edge_id)
        port = str(raw.get("port") or "out")
        edges.append({
            "id": edge_id,
            "source": ids.get(str(raw.get("source") or ""), str(raw.get("source") or "")),
            "target": ids.get(str(raw.get("target") or ""), str(raw.get("target") or "")),
            "port": port if port in {"out", "true", "false"} else "out",
        })
    graph = FlowGraph.model_validate({"nodes": prepared, "edges": edges})

    # report-analysis starts a fresh datasource analysis. When the model put it
    # after extraction without a source, preserve the goal in an upstream LLM.
    node_map = {node.id: node for node in graph.nodes}
    parents = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.target in parents and edge.source in node_map:
            parents[edge.target].append(node_map[edge.source])
    converted = []
    for node in graph.nodes:
        if (
            node.kind == "report-analysis"
            and not node.config.get("datasourceId")
            and any(parent.kind != "start" for parent in parents[node.id])
        ):
            question = str(node.config.get("query") or prompt).strip()
            config = hydrate_editable_config("llm", {
                "systemPrompt": "只分析上游传入的真实数据；明确区分事实、推断与建议，不编造缺失数据。",
                "promptTemplate": f"分析目标：{question}\n\n上游数据：\n{{{{ input }}}}",
                "temperature": 0.1,
            })
            converted.append(node.model_copy(update={"kind": "llm", "config": config}))
        else:
            converted.append(node)
    graph = FlowGraph(nodes=converted, edges=graph.edges)

    # reply and end are both terminal. Models frequently append one after the
    # other, which creates an impossible outgoing edge on a terminal node.
    node_map = {node.id: node for node in graph.nodes}
    incoming = {node.id: [] for node in graph.nodes}
    outgoing = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.target in incoming:
            incoming[edge.target].append(edge)
        if edge.source in outgoing:
            outgoing[edge.source].append(edge)
    redundant = {
        edge.target
        for edge in graph.edges
        if node_map.get(edge.source) is not None
        and node_map.get(edge.target) is not None
        and node_map[edge.source].kind in {"end", "reply"}
        and node_map[edge.target].kind in {"end", "reply"}
        and len(incoming[edge.target]) == 1
        and not outgoing[edge.target]
    }
    if redundant:
        graph = FlowGraph(
            nodes=[node for node in graph.nodes if node.id not in redundant],
            edges=[edge for edge in graph.edges if edge.source not in redundant and edge.target not in redundant],
        )
    return _bind_selected_resources(_readable_generated_layout(graph), prompt, resources or [])


def _fallback_graph(payload: WorkflowGenerateInput, resources: list[dict]) -> FlowGraph:
    """Create a safe editable draft when both model attempts are unusable.

    中文补充：模型连续失败时的确定性兜底。按提示词里的关键词与所选资源类型选三种雏形：
    有 HTTP 采集意图（或只选了 http 资源）-> start/http-request/llm/reply；
    只选了数据库类资源 -> start/report-analysis/reply；都没有 -> start/llm/reply。
    雏形里只带安全的系统提示词与用户的业务目标原文，不编造数据源、字段或结论；
    生成后仍走 ``_normalize_generated_graph``，因此结果一定满足图契约（可直接编辑与保存）。
    """
    text = payload.prompt.casefold()
    has_http_intent = any(word in text for word in ("http", "api", "接口", "请求", "抓取", "采集", "豆瓣"))
    http_resources = [item for item in resources if item["type"] == "http"]
    data_resources = [item for item in resources if item["type"] != "http"]
    if has_http_intent or (http_resources and not data_resources):
        nodes = [
            {"id": "start", "kind": "start", "title": "开始"},
            {"id": "collect", "kind": "http-request", "title": "采集 HTTP 数据"},
            {"id": "analyse", "kind": "llm", "title": "分析采集结果", "config": {
                "systemPrompt": "只分析上游传入的真实数据，不编造缺失数据。",
                "promptTemplate": f"分析目标：{payload.prompt}\n\n上游数据：\n{{{{ input }}}}",
                "temperature": 0.1,
            }},
            {"id": "reply", "kind": "reply", "title": "输出分析结果"},
        ]
    elif data_resources:
        nodes = [
            {"id": "start", "kind": "start", "title": "开始"},
            {"id": "analyse", "kind": "report-analysis", "title": "分析业务数据", "config": {
                "query": payload.prompt,
            }},
            {"id": "reply", "kind": "reply", "title": "输出分析结果"},
        ]
    else:
        nodes = [
            {"id": "start", "kind": "start", "title": "开始"},
            {"id": "analyse", "kind": "llm", "title": "处理业务目标", "config": {
                "systemPrompt": "严格依据运行输入完成用户目标；缺少业务数据时明确说明。",
                "promptTemplate": f"业务目标：{payload.prompt}\n\n运行输入：\n{{{{ input }}}}",
                "temperature": 0.1,
            }},
            {"id": "reply", "kind": "reply", "title": "输出结果"},
        ]
    edges = [
        {"id": f"fallback_{index + 1}", "source": left["id"], "target": right["id"], "port": "out"}
        for index, (left, right) in enumerate(zip(nodes, nodes[1:]))
    ]
    return _normalize_generated_graph({"nodes": nodes, "edges": edges}, prompt=payload.prompt, resources=resources)


def _start_model_call(ctx, run_id: str, attempt: int) -> ModelCall:
    """写一条"已开始"的模型调用记录并提交，保证提供方失败后仍能追溯本次生成尝试。

    参数 ``attempt`` 记入 metadata，便于区分首次与修复重试；返回值由 ``_finish_model_call`` 收尾。
    """
    call = ModelCall(
        id=new_id(), workspace_id=ctx.workspace_id, run_id=run_id, step_id=None,
        provider="pending", model="reasoning", status="started", usage_json=None,
        metadata_json={"entry": "workflow_generation", "attempt": attempt},
        started_at=datetime.utcnow(), completed_at=None,
    )
    ctx.db.add(call)
    ctx.db.commit()
    return call


def _finish_model_call(ctx, call_id: str, *, response: dict | None = None, error: Exception | None = None) -> None:
    """收尾模型调用记录：成功写入 provider/model/usage，失败记录异常类名。

    记录已不存在（例如事务已回滚后重建）时静默返回，不抛异常——收尾失败不应掩盖原始错误。
    """
    call = ctx.db.get(ModelCall, call_id)
    if call is None:
        return
    if response:
        call.provider = str(response.get("provider") or "openai-compatible")[:64]
        call.model = str(response.get("model") or "reasoning")[:200]
        call.usage_json = response.get("usage") or {}
    call.status = "failed" if error else "completed"
    call.error_code = type(error).__name__[:100] if error else None
    call.completed_at = datetime.utcnow()
    ctx.db.commit()


def _usage_total(usages: list[dict]) -> dict:
    """把多次尝试的 usage 按数值键累加（只在所有条目中出现过的数值键）。

    非数值字段（如模型名）自然不会进入结果，避免把字符串相加。
    """
    keys = {key for usage in usages for key, value in usage.items() if isinstance(value, (int, float))}
    return {key: sum(usage.get(key, 0) for usage in usages) for key in keys}


def _require_editable_structure(graph: FlowGraph) -> None:
    """Retry topology mistakes while allowing explicit property placeholders.

    中文补充：只把**非配置类**的校验错误视为需要重试的模型错误（如缺 start、
    终止节点有出线、悬空连线）。以"节点配置不完整或类型错误："开头的错误被排除，
    因为那属于"等待用户在画布上选资源"的正常草稿状态——模型无法提供它没有的数据源 id。
    异常：ModelError（最多附带 5 条结构错误摘要）。
    """
    structural = [
        item["message"] for item in inspect_graph(graph)["errors"]
        if not item["message"].startswith("节点配置不完整或类型错误：")
    ]
    if structural:
        raise ModelError("模型工作流结构未通过校验：" + "；".join(structural[:5]))


def _workflow_result(
    payload: WorkflowGenerateInput, graph: FlowGraph, run_id: str, *,
    mode: str, attempts: int, model_name: str, usage: dict, warnings: list[str],
    raw: dict | None = None,
) -> dict:
    """组装返回给前端的生成结果（不含持久化）。

    结构：``runId / workflow{name,description,domains,status,graph,validation} /
    model / usage / generation{mode,attempts,warnings}``。
    ``mode`` 取值 model（首次成功）、repaired（重试后成功）、fallback（确定性兜底）。
    图仍有未配置项时追加一条 warning 说明还需确认多少项，warnings 会去重。
    名称/描述优先取模型给的值，缺失时回退到用户填写的名称与原始目标。
    """
    validation = inspect_graph(graph)
    if not validation["valid"]:
        warnings.append(f"草稿还有 {len(validation['errors'])} 项资源或节点属性需要确认，已定位到第一个待配置节点。")
    return {
        "runId": run_id,
        "workflow": {
            "name": str((raw or {}).get("name") or payload.name)[:200],
            "description": str((raw or {}).get("description") or payload.prompt)[:2000],
            "domains": payload.domains,
            "status": "draft",
            "graph": graph.model_dump(mode="json", by_alias=True),
            "validation": validation,
        },
        "model": model_name,
        "usage": usage,
        "generation": {
            "mode": mode,
            "attempts": attempts,
            "warnings": list(dict.fromkeys(warnings)),
        },
    }


def generate(ctx, payload: WorkflowGenerateInput) -> dict:
    """Generate an editable draft; never silently save, publish or execute it.

    中文补充：``POST /api/v1/workflows/generate``（需要 ``workflows.create``）。
    返回草稿数据并把每次尝试记录到统一运行中心（entry=workflow_generation），
    成功/兜底时结束运行为 completed，两次都失败且兜底也失败时结束为 failed 并抛原始异常。

    系统提示词的契约（修改时必须与 ``_normalize_generated_graph``/``inspect_graph`` 的规则同步）：
    - 只返回一个 JSON 对象，结构为 ``{name,description,domains,graph:{nodes,edges}}``；
    - 恰好一个 start，至少一个 end 或 reply，节点可从 start 到达且最终到达输出；
      end 与 reply 都是终止节点，二选一作为分支末端，禁止 end→reply/reply→end；
    - 每个节点必须有 id/kind/title/position/inputs/config，创建时先完整复制该类型的
      defaultConfig 再按业务目标修改（防止模型省略数组/表达式结构）；
    - database-query 与 report-analysis 会主动查询数据源，不能用于分析上游结果；
    - 只允许使用用户明确选择的数据源目录中的 id 与版本，目录为空时 datasourceId 必须留空；
      不得臆造数据源、知识库、Bot、工具或子流程 id，不生成凭据、密钥或演示数据。

    重试：失败后把上次输出与校验摘要作为 user 消息追加，要求"完整重写 JSON、不要解释"。
    失败兜底见模块 docstring。异常：权限异常、ValidationError、兜底失败时重新抛出。
    副作用：写 ModelCall、统一运行与审计；**不写工作流表**（用户需自行保存草稿）。
    """
    require_permission(ctx, "workflows.create")
    resources = _resource_catalog(ctx, payload.resource_ids)
    available = [
        {
            "kind": item["kind"], "title": item["title"],
            "description": item["description"], "defaultConfig": item["defaultConfig"],
            "configSchema": item["configSchema"],
        }
        for item in node_catalog() if item["available"]
    ]
    run = runtime.create_run(
        ctx.db, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
        conversation_id="", model_profile="reasoning",
        context_snapshot={
            "entry": "workflow_generation", "domains": payload.domains,
            "selectedResourceIds": payload.resource_ids,
        },
    )
    run.status = "running"
    ctx.db.commit()
    system = (
        "你是企业工作流架构师。只返回一个 JSON 对象，禁止 Markdown 和解释。"
        "对象结构必须为 {name,description,domains,graph:{nodes,edges}}。"
        "图必须有且仅有一个 start，并至少有一个 end 或 reply；所有节点可从 start 到达且最终到达输出。"
        "end 和 reply 都是终止节点，二选一作为每条分支末端，不能有出线，禁止 reply→end 或 end→reply。"
        "每个节点必须填写 id、kind、title、position、inputs、config。"
        "节点 id 以英文字母开头且唯一，position 为 {x,y}，inputs 为对象，config 严格遵循目录 schema。"
        "创建节点时先完整复制该类型的 defaultConfig，再按业务目标修改；数组、对象、版本号和表达式结构不得省略。"
        "database-query 与 report-analysis 都会主动查询所选数据源，不能用来分析上游节点结果；"
        "分析 HTTP 或其他上游结果应使用 arithmetic、aggregator、rule-evaluate、llm 或 extractor，并通过连线或 inputs 引用。"
        "连线为 {id,source,target,port}；普通 port=out，condition 必须同时连接 true 和 false。"
        "只允许使用用户明确选择的数据源目录中的 ID 与版本；目录为空时 datasourceId 必须留空。"
        "不得臆造数据源、知识库、Bot、工具或子流程 ID。不要生成凭据、密钥或演示数据。\n"
        "用户明确选择的数据源目录：" + json.dumps(resources, ensure_ascii=False) + "\n"
        "可用节点目录：" + json.dumps(available, ensure_ascii=False)
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": payload.prompt},
    ]
    failures: list[str] = []
    usages: list[dict] = []
    last_model = "reasoning"
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        call = _start_model_call(ctx, run.id, attempt)
        response = None
        try:
            response = model.chat_for_run(
                "reasoning", messages, temperature=0.1, workspace_id=ctx.workspace_id,
                usage_context={"source": "workflow_generation", "source_id": run.id, "attempt": attempt},
            )
            usages.append(response.get("usage") or {})
            last_model = str(response.get("model") or "reasoning")[:200]
            raw = _json_object(str(response.get("content") or ""))
            graph = _normalize_generated_graph(raw.get("graph") or {}, prompt=payload.prompt, resources=resources)
            _require_editable_structure(graph)
            # 授权检查在返回前完成：模型可能引用越权资源，必须当场发现而不是等到用户点保存。
            authorize_graph(ctx, graph)
            _finish_model_call(ctx, call.id, response=response)
            mode = "model" if attempt == 1 else "repaired"
            warnings = [] if attempt == 1 else ["首次模型输出未通过工作流契约，系统已自动重试并修复。"]
            result = _workflow_result(
                payload, graph, run.id, mode=mode, attempts=attempt,
                model_name=last_model, usage=_usage_total(usages), warnings=warnings, raw=raw,
            )
            current_run = ctx.db.get(type(run), run.id)
            runtime.finalize_run(ctx.db, current_run, status="completed", model_usage=result["usage"])
            commit_change(ctx, "workflow.ai.generate", "workflows", run.id, {
                "node_count": len(graph.nodes), "valid": result["workflow"]["validation"]["valid"],
                "mode": mode, "attempts": attempt, "selected_resource_count": len(resources),
            })
            return result
        except Exception as exc:
            ctx.db.rollback()
            failures.append(_safe_error(exc))
            _finish_model_call(ctx, call.id, response=response, error=exc)
            if response and response.get("content"):
                messages.extend([
                    {"role": "assistant", "content": str(response["content"])[:20000]},
                    {"role": "user", "content": (
                        "上次输出未通过工作流契约。请完整重写 JSON，不要解释。"
                        "确保每个节点含 title/position/inputs/config，不在节点内添加 validation，"
                        "并且终止节点没有出线。校验摘要：" + failures[-1]
                    )},
                ])

    # Provider outages and malformed responses should not destroy the user's
    # work. Return a deterministic, editable contract-valid draft instead.
    try:
        graph = _fallback_graph(payload, resources)
        authorize_graph(ctx, graph)
        result = _workflow_result(
            payload, graph, run.id, mode="fallback", attempts=MAX_GENERATION_ATTEMPTS,
            model_name="deterministic-fallback", usage=_usage_total(usages),
            warnings=["模型连续两次未返回可用流程；系统已保留业务目标并生成安全的可编辑草稿。"],
        )
        current_run = ctx.db.get(type(run), run.id)
        runtime.finalize_run(ctx.db, current_run, status="completed", model_usage=result["usage"])
        commit_change(ctx, "workflow.ai.generate", "workflows", run.id, {
            "node_count": len(graph.nodes), "valid": result["workflow"]["validation"]["valid"],
            "mode": "fallback", "attempts": MAX_GENERATION_ATTEMPTS,
            "failure_types": failures, "selected_resource_count": len(resources),
        })
        return result
    except Exception as exc:
        ctx.db.rollback()
        current_run = ctx.db.get(type(run), run.id)
        if current_run:
            runtime.finalize_run(
                ctx.db, current_run, status="failed", error_code="workflow_generation_failed",
                error_summary=_safe_error(exc),
            )
            ctx.db.commit()
        raise
