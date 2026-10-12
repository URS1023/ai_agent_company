"""Assemble and atomically publish a ready quality workflow from ordinary form data.

职责：质检"普通业务向导"的后端 —— 用户按"设备 + 模板修订 + 逐指标检测服务"填表，
本模块编译成完整画布并**一次性**发布（流程 + 首修订 + 幂等回执 + 审计，同一事务）。

位置与依赖：被 ``quality.router`` 调用；向下依赖 ``quality.registry``（模板/设备校验）、
``quality.detection``（检测节点预检）、``quality.run_plans``（发布后生成运行方案回显）、
``workflows``（图校验、修订与发布）。

对外语义与约束：
- 权限：``quality.view`` / ``quality.create`` / ``workflows.view`` /
  ``workflows.create`` / ``workflows.publish``。
- 幂等：``request_key`` + ``request_hash``；同键同配置返回既有结果（``reused=True``），
  同键不同配置或流程后来被改动则 409。
- 画布是"数据流"形态：start →（每组：各指标检测节点 → 可选汇合 → 判定）→ 可选汇合 → 可选写入 → end；
  每组使用独立输入字段 ``samplesG01``、``samplesG02``…，避免多设备组互相串数据。
- 预检失败不留半成品：所有引用、指标覆盖、检测服务可用性在写库前校验。
"""
from __future__ import annotations

from hashlib import sha256
import json

from sqlalchemy import select

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.errors import ConflictError, ValidationError
from app.modules.quality.detection import validate_detector
from app.modules.quality.models import QualityWorkflowCreation
from app.modules.quality.registry import validate_inspection
from app.modules.quality.run_plans import build_plan
from app.modules.quality.schemas import DetectorConfig, QualityConfig
from app.modules.quality.workflow_schemas import QualityWorkflowInput, QualityWorkflowView
from app.modules.system.service_helpers import commit_change, flush_changes
from app.modules.workflows.graph import executable_graph
from app.modules.workflows.models import Workflow
from app.modules.workflows.schemas import FlowGraph
from app.modules.workflows.service import add_revision, get_workflow, workflow_view


def _request_hash(payload: QualityWorkflowInput) -> str:
    """计算"除防重复键外"的配置摘要，用于识别同键不同配置的重复提交（409）。

    排序键 + 紧凑分隔符，保证同一份配置在任何进程/时机得到同一摘要。
    与预警向导的同类函数相比少了 ``allow_nan=False``（质检表单不含浮点输入），
    改这里时注意两处不要假定行为一致。
    """
    document = payload.model_dump(by_alias=True, mode="json", exclude={"request_key"})
    return sha256(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _node(node_id: str, kind: str, title: str, x: int, y: int, *, config=None, inputs=None) -> dict:
    """构造画布节点（质检版：x/y 都显式给，按"组"分层排布，y 随组序号下移）。

    ``title`` 截断到 160 字符；``inputs`` 用于把开始节点的样本数组映射进检测节点。
    """
    return {"id": node_id, "kind": kind, "title": title[:160], "position": {"x": x, "y": y},
            "inputs": inputs or {}, "config": config or {}}


def _edge(source: str, target: str) -> dict:
    """构造顺序边；边 id 由两端节点 id 拼成，故同一对节点不能有两条边。"""
    return {"id": f"edge_{source}_{target}", "source": source, "target": target, "port": "out"}


def _build_graph(ctx: AuthContext, payload: QualityWorkflowInput) -> FlowGraph:
    """Resolve every business reference before producing a graph or writing records.

    （中文补充）按组编译画布并做全量预检：
    1) 每组校验设备/模板修订（``validate_inspection``，要求 active 且设备启用 quality 域）；
    2) 该组提交的指标集合必须与模板修订的指标**完全一致**（不遗漏/不重复/不额外）；
    3) 数值类人工指标暂不支持自动采集，直接拒绝并提示改用外观/成分模板或人工录入；
    4) 每个指标生成一个检测节点（``qc-appearance`` / ``qc-component``），
       配置固定为上游输入模式，样本/图片字段名固定为 ``sampleId`` / ``imageFileId``，
       并在建图前用 ``validate_detector`` 预检检测服务可用性；
    5) 多检测节点时插入 ``merge`` 汇合证据，再连到该组的 ``qc-evaluate`` 判定节点
       （固定 ``sampleMode='input'`` 逐样本出报告）；
    6) 多组时再插入一个总汇合节点，可选追加写入节点，最后接 ``end``。
    坐标按组序号与指标序号递推，保证不重叠。返回 ``FlowGraph``（已通过结构与可执行性校验）。
    异常：指标不齐、含不支持类别、设备/模板/检测服务不满足要求时抛 ``ValidationError``。
    """
    nodes = [_node("start", "start", "开始 · 提交设备样本", 40, 80)]
    edges: list[dict] = []
    evaluators: list[str] = []
    for group_index, group in enumerate(payload.groups, 1):
        standard, equipment = validate_inspection(ctx, QualityConfig(
            templateId=group.template_id, templateRevision=group.template_revision,
            deviceId=group.device_id, sampleMode="input",
        ), active=True)
        expected = {(item["id"], item["version"]): item for item in standard.snapshot["metrics"]}
        supplied = {(item.metric_id, item.metric_version): item for item in group.detectors}
        if set(supplied) != set(expected):
            raise ValidationError("每个设备标准组必须完整且仅覆盖所选模板修订的全部指标")
        unsupported = [item["name"] for item in expected.values() if item["category"] not in {"appearance", "component"}]
        if unsupported:
            raise ValidationError("普通自动流程尚不能采集数值人工指标，请改用外观/成分模板或在质检记录页人工录入",
                                  metrics=unsupported)
        base_y = 80 + (group_index - 1) * 300
        detector_ids: list[str] = []
        input_key = f"samplesG{group_index:02d}"
        for metric_index, metric in enumerate(standard.snapshot["metrics"], 1):
            binding = supplied[(metric["id"], metric["version"])]
            kind = "qc-appearance" if metric["category"] == "appearance" else "qc-component"
            config = DetectorConfig(
                metricId=binding.metric_id, metricVersion=binding.metric_version,
                deviceId=equipment.id, datasourceId=binding.datasource_id,
                datasourceVersion=binding.datasource_version, requestMode="input",
                sampleField="sampleId", imageField="imageFileId",
            )
            validate_detector(ctx, kind, config, active=True)
            detector_id = f"detect_g{group_index:02d}_m{metric_index:02d}"
            detector_ids.append(detector_id)
            nodes.append(_node(detector_id, kind, metric["name"] + (" · 外观检测" if kind == "qc-appearance" else " · 成分检测"),
                               320, base_y + (metric_index - 1) * 92,
                               config=config.model_dump(by_alias=True, mode="json"),
                               inputs={"input": {"nodeId": "start", "path": "$." + input_key}}))
            edges.append(_edge("start", detector_id))
        evidence_id = detector_ids[0]
        if len(detector_ids) > 1:
            evidence_id = f"evidence_g{group_index:02d}"
            nodes.append(_node(evidence_id, "merge", "汇合完整检测证据", 620,
                               base_y + (len(detector_ids) - 1) * 46))
            edges.extend(_edge(detector_id, evidence_id) for detector_id in detector_ids)
        evaluator_id = f"evaluate_g{group_index:02d}"
        group_title = group.title or f"{equipment.name} · {standard.snapshot['name']}"
        nodes.append(_node(evaluator_id, "qc-evaluate", group_title, 900,
                           base_y + (len(detector_ids) - 1) * 46,
                           config={"deviceId": equipment.id, "templateId": standard.template_id,
                                   "templateRevision": standard.revision, "sampleId": "", "sampleMode": "input"}))
        edges.append(_edge(evidence_id, evaluator_id))
        evaluators.append(evaluator_id)
    output_id = evaluators[0]
    if len(evaluators) > 1:
        output_id = "reports"
        nodes.append(_node(output_id, "merge", "汇合全部质检报告", 1180, 80 + (len(payload.groups) - 1) * 150))
        edges.extend(_edge(evaluator_id, output_id) for evaluator_id in evaluators)
    if payload.writeback is not None:
        write_id = "write_reports"
        nodes.append(_node(write_id, "output-table", "写入授权业务结果表", 1460,
                           80 + (len(payload.groups) - 1) * 150,
                           config=payload.writeback.model_dump(by_alias=True, mode="json")))
        edges.append(_edge(output_id, write_id))
        output_id = write_id
    end_x = 1740 if payload.writeback is not None else 1460
    nodes.append(_node("end", "end", "完成 · 保存报告与追溯", end_x, 80 + (len(payload.groups) - 1) * 150))
    edges.append(_edge(output_id, "end"))
    graph = FlowGraph.model_validate({"nodes": nodes, "edges": edges})
    executable_graph(ctx, graph)
    return graph


def create_published(ctx: AuthContext, payload: QualityWorkflowInput) -> QualityWorkflowView:
    """Preflight all sources, then persist workflow, revision, receipt and audit in one commit.

    （中文补充）向导唯一写入口：需 ``quality.view`` / ``quality.create`` / ``workflows.view`` /
    ``workflows.create`` / ``workflows.publish``。
    幂等回执命中且摘要一致、流程仍是当时发布的修订时，直接返回既有结果（``reused=True``，
    并重新生成运行方案）；摘要不同或流程已被改动则抛 ``ConflictError``。
    未命中则编译画布，创建 ``status='published'``、``version=1`` / ``published_revision=1`` 的流程，
    追加首修订（reason="质量检查向导创建并发布"，与调度绑定校验的历史发布原因一致），
    写回执、生成运行方案并落审计。返回 ``QualityWorkflowView``。
    副作用：写 workflow_definitions / workflow_revisions / quality_workflow_creations 并提交；
    任一步失败整体回滚。
    """
    for permission in ("quality.view", "quality.create", "workflows.view", "workflows.create", "workflows.publish"):
        require_permission(ctx, permission)
    digest = _request_hash(payload)
    receipt = ctx.db.scalar(select(QualityWorkflowCreation).where(
        QualityWorkflowCreation.workspace_id == ctx.workspace_id,
        QualityWorkflowCreation.owner_user_id == ctx.user_id,
        QualityWorkflowCreation.request_key == payload.request_key,
    ))
    if receipt is not None:
        if receipt.request_hash != digest:
            raise ConflictError("此防重复键已用于另一份检测流程配置，请核对已创建结果", workflow_id=receipt.workflow_id)
        row = get_workflow(ctx, receipt.workflow_id)
        if row.status != "published" or row.published_revision != receipt.published_revision:
            raise ConflictError("此请求已创建流程，但流程后来已修改或停用，请直接打开既有流程核对",
                                workflow_id=row.id)
        plan, _ = build_plan(ctx, row.id)
        return QualityWorkflowView(workflow=workflow_view(row), runPlan=plan, reused=True)

    graph = _build_graph(ctx, payload)
    row = Workflow(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, version=1,
                   name=payload.name, description=payload.description, domains=["quality"],
                   graph=graph.model_dump(by_alias=True, mode="json"), status="published", published_revision=1)
    ctx.db.add(row)
    flush_changes(ctx)
    add_revision(ctx, row, "质量检查向导创建并发布")
    ctx.db.add(QualityWorkflowCreation(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
               workflow_id=row.id, request_key=payload.request_key, request_hash=digest, published_revision=1))
    flush_changes(ctx)
    plan, _ = build_plan(ctx, row.id)
    commit_change(ctx, "quality.workflow.publish", "workflows", row.id,
                  {"revision": 1, "groups": len(payload.groups), "writeback": payload.writeback is not None})
    return QualityWorkflowView(workflow=workflow_view(row), runPlan=plan, reused=False)
