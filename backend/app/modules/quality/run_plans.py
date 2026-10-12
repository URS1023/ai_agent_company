"""Derive an ordinary multi-device form from published workflow bindings without rewriting its graph.

职责：把已发布质检流程**推导成"普通运行页"能用的运行方案**（设备标准组、指标、写入目标），
供运行页预览与提交时比对；**不修改流程画布**，只读推导。

位置与依赖：被 ``quality.runs``（提交前比对）与 ``quality.workflow_builder``（发布后回显）
调用；依赖 ``registry.validate_inspection`` 复核设备/模板，``alerts.registry.record_view``
序列化设备，``workflows.service.revision`` 取发布修订快照。

对外约束（"普通运行页"的能力边界，超出即拒绝并提示改去工作流页面）：
- 只接受 ``FORM_KINDS`` 里的节点类型（开始/结束/回复/汇合/外观检测/成分检测/判定/写入）；
- 检测节点必须**直接接开始节点**（前置变换/复杂 JSONPath 映射一律拒绝）；
- 判定节点必须是"按上游样本分别生成报告"模式（``sampleMode='input'``）；
- 每个检测节点只能属于一个设备标准组，且检测节点与判定节点必须同设备；
- 每个设备标准组必须**完整且仅覆盖**所选模板修订的全部指标（不能漏、不能多、不能重复）；
- 样本输入字段不得使用平台保留字（``RESERVED``），不同组必须用不同输入字段。
"""
import re

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.errors import ValidationError
from app.modules.alerts.registry import record_view
from app.modules.quality.registry import validate_inspection
from app.modules.quality.run_schemas import QualityRunPlan
from app.modules.quality.schemas import DetectorConfig, QualityConfig
from app.modules.workflows.graph import executable_graph
from app.modules.workflows.schemas import FlowGraph, FlowNode
from app.modules.workflows.service import get_workflow, revision

# 保留字段：__qualityForm 是运行表单随执行保存的键；其余三个用于阻断原型污染类输入。
RESERVED = {"__qualityForm", "__proto__", "prototype", "constructor"}
# 普通运行页允许出现的节点类型白名单。
FORM_KINDS = {"start", "end", "reply", "merge", "qc-appearance", "qc-component", "qc-evaluate", "output-table"}


def detector_input(node: FlowNode, graph: FlowGraph) -> str:
    """Accept one explicit start field, or the normal root data packet; never execute a JSONPath setter.

    （中文补充）确定该检测节点从开始节点的哪个字段取样本数组，返回字段名（无映射时返回 ``data``）。
    只接受两种形态：无 ``inputs``（用默认根数据包），或唯一一个 ``input`` 映射指向开始节点、
    路径为 ``$`` 或 ``$.字段名``。其余（多映射、非开始节点来源、复杂路径）一律拒绝 ——
    普通运行页不解释任意 JSONPath，避免出现"看起来配好了其实取不到数据"的静默失败。
    异常：形状不符或字段命中 ``RESERVED`` 时抛 ``ValidationError``。
    """
    starts = [item.id for item in graph.nodes if item.kind == "start"]
    parents = [edge.source for edge in graph.edges if edge.target == node.id]
    if parents != starts:
        raise ValidationError("普通质检运行要求检测节点直接接开始节点；含前置变换请在工作流页面运行")
    if not node.inputs:
        return "data"
    reference = node.inputs.get("input")
    if set(node.inputs) != {"input"} or reference.node_id != starts[0]:
        raise ValidationError("检测节点需将开始节点的一个样本数组映射到 input")
    if reference.path == "$":
        return "data"
    if not re.fullmatch(r"\$\.[A-Za-z_][A-Za-z0-9_]*", reference.path):
        raise ValidationError("普通运行的样本输入使用 $.字段名；复杂映射仍可在工作流页面运行")
    key = reference.path[2:]
    if key in RESERVED:
        raise ValidationError("样本输入字段与平台保留字段冲突")
    return key


def detector_ancestors(node_id: str, graph: FlowGraph) -> list[FlowNode]:
    """Require direct detector/merge evidence, preserving the exact per-sample reference contract.

    （中文补充）从判定节点向上游收集"直接产生证据"的检测节点：允许穿过**无输入的**
    汇合/判定节点（``merge`` / ``qc-evaluate``）继续上溯，遇到其他节点类型即拒绝 ——
    即不允许在检测与判定之间插入筛选、改写等步骤，否则"逐样本检测引用"的对应关系会被破坏。
    返回至少一个检测节点；异常：路径上出现非白名单节点、或没有关联到任何检测节点时抛
    ``ValidationError``。只读遍历，不执行图。
    """
    nodes = {node.id: node for node in graph.nodes}
    result: dict[str, FlowNode] = {}
    pending = [node_id]
    visited = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        node = nodes[current]
        if node.kind in {"qc-appearance", "qc-component"}:
            result[current] = node
        elif node.kind in {"merge", "qc-evaluate"} and not node.inputs:
            pending.extend(edge.source for edge in graph.edges if edge.target == current)
        else:
            raise ValidationError("逐样本判定前使用检测节点或默认结果汇合；不能隐藏筛选或改写原始检测引用")
    if not result:
        raise ValidationError("判定节点尚未关联实际检测节点")
    return list(result.values())


def build_plan(ctx: AuthContext, workflow_id: str) -> tuple[QualityRunPlan, FlowGraph]:
    """Read authorized published configuration and present every device, standard and write target.

    （中文补充）需要 ``quality.view``；返回 ``(运行方案, 已校验可执行的画布)``。
    只针对**已发布且未停用**的流程，按 ``published_revision`` 取快照（不跟随最新草稿）。
    逐节点处理：``output-table`` 解析目标表与数据源（生成 ``writes``，需用户单独确认）；
    ``qc-evaluate`` 必须是逐样本模式，逐个检测节点校验（上游输入模式、同设备、不重复绑定、
    输入字段不冲突且非保留字），并校验指标集合与模板修订**完全一致**（不遗漏/不重复/不额外）。
    异常：流程未发布、发布修订未启用 quality 域、含不支持的节点类型、判定节点模式不符、
    检测节点归属冲突、指标不齐、组数不在 1–30 或存在未连接的检测节点时抛 ``ValidationError``。
    只读，不写库、不执行检测。
    """
    require_permission(ctx, "quality.view")
    workflow = get_workflow(ctx, workflow_id)
    if workflow.status != "published" or workflow.published_revision is None:
        raise ValidationError("请选择已发布且未停用的检测流程")
    selected = revision(ctx, workflow, workflow.published_revision)
    graph = FlowGraph.model_validate(selected.snapshot["graph"])
    executable_graph(ctx, graph)
    if "quality" not in selected.snapshot["domains"]:
        raise ValidationError("流程发布修订未启用质量检查用途")
    if any(node.kind not in FORM_KINDS for node in graph.nodes):
        raise ValidationError("此流程包含需要额外业务输入的节点，请使用工作流页面；普通质检支持开始、检测、汇合、判定、写入和结束节点")
    groups, assigned, buckets, writes = [], set(), {}, []
    for node in graph.nodes:
        if node.kind == "output-table":
            from app.modules.writeback.targets import get_target
            target, source = get_target(ctx, node.config["targetId"])
            writes.append({"nodeId": node.id, "name": target.name, "datasourceName": source.name,
                           "tableName": target.table_name, **node.config})
        if node.kind != "qc-evaluate":
            continue
        config = QualityConfig.model_validate(node.config)
        if config.sampleMode != "input":
            raise ValidationError("请将质检判定节点改为“按上游样本分别生成报告”，保存并发布后再运行")
        standard, equipment = validate_inspection(ctx, config, active=True)
        detectors = []
        for detector in detector_ancestors(node.id, graph):
            detector_config = DetectorConfig.model_validate(detector.config)
            if detector_config.requestMode != "input" or detector_config.deviceId != config.deviceId:
                raise ValidationError("检测节点须使用上游样本模式，并与判定节点属于同一设备")
            if detector.id in assigned:
                raise ValidationError("同一检测节点不能绑定到多个运行组，请明确各设备标准组的检测链")
            assigned.add(detector.id)
            key = detector_input(detector, graph)
            if key in buckets and buckets[key] != node.id:
                raise ValidationError("不同设备标准组必须使用不同开始输入字段，例如 $.samplesA 和 $.samplesB")
            buckets[key] = node.id
            if {detector_config.sampleField, detector_config.imageField} & RESERVED or detector_config.sampleField == detector_config.imageField:
                raise ValidationError("样本和图片字段必须不同，且不能使用平台保留字段")
            detectors.append({"nodeId": detector.id, "kind": detector.kind, "inputKey": key,
                              **detector_config.model_dump()})
        actual = [(item["metricId"], item["metricVersion"]) for item in detectors]
        expected = [(item["id"], item["version"]) for item in standard.snapshot["metrics"]]
        if len(set(actual)) != len(actual) or set(actual) != set(expected):
            raise ValidationError("普通自动检测需完整覆盖所选模板的全部指标及固定版本；不能遗漏、重复或增加模板外指标")
        groups.append({"nodeId": node.id, "title": node.title, "device": record_view(ctx, "devices", equipment),
                       "template": standard.snapshot, "templateRevision": standard.revision,
                       "imageRequired": any(item["kind"] == "qc-appearance" for item in detectors), "detectors": detectors})
    expected_nodes = {node.id for node in graph.nodes if node.kind in {"qc-appearance", "qc-component"}}
    if not groups or len(groups) > 30 or assigned != expected_nodes:
        raise ValidationError("运行方案需要 1–30 个完整的设备标准组，所有检测节点均须连接到一个质检判定节点")
    return QualityRunPlan(workflowId=workflow.id, name=selected.snapshot["name"], version=workflow.version,
                          publishedRevision=selected.revision, groups=groups, writes=writes), graph
