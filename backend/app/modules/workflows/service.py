"""Versioned workflow lifecycle; published execution never follows an edited draft.

中文补充：工作流的生命周期服务层（``service.py`` 只被 ``router.py`` 与 ``execution.py`` 调用）。
核心不变式：**草稿可变、修订不可变、正式运行固定修订**。

- 每次保存/发布/停用/恢复都会 ``version += 1`` 并追加一条 ``workflow_revisions``，
  同时写审计（``commit_change``）；调用方必须传乐观锁版本，过期返回 409。
- 发布（``change_status(publish=True)``）先跑 ``executable_graph``（结构 + 资源授权）；
  停用只停止后续正式运行，不假装中断已发出的外部请求。
- 恢复历史修订是"复制成新草稿"，当前 ``published_revision`` 保持不变。
- 删除是软删除（status='deleted'），保留修订与运行历史。
- 列表接口在"授权通过"之后才计入分页，避免通过总数或空页泄露无权记录的规模；
  逐条调用 ``authorize_graph`` 意味着权限撤销会立即反映到列表与详情。
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError

from app.common.authorization import require_permission
from app.common.owned_records import owned_record, visible_records
from app.errors import NotFoundError, PermissionErrorEx, ValidationError
from app.modules.alerts import registry as equipment_registry
from app.modules.workflows.catalog import editable_config
from app.modules.system.service_helpers import check_version, commit_change, flush_changes
from app.modules.workflows.graph import authorize_graph, executable_graph, inspect_graph
from app.modules.workflows.models import DeviceWorkflowBinding, Workflow, WorkflowRevision
from app.modules.workflows.schemas import (
    DeviceWorkflowEntryInput, DeviceWorkflowEntryView, FlowGraph, FlowNode, Position,
    WorkflowInput, WorkflowView,
)


def _utc_timestamp(value: datetime) -> str:
    """把数据库里的 naive datetime 视为 UTC，统一输出带时区的 ISO 字符串。

    历史上部分驱动会返回 naive 值，因此这里显式补上 UTC 而不是依赖本地时区，
    保证前端展示与 ``period_bounds`` 的统计口径一致。
    """
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()


def workflow_view(row: Workflow) -> WorkflowView:
    """把流程记录投影成接口视图，并实时附带当前图的校验结果。

    ``validation`` 每次调用都重新计算（``inspect_graph``）：草稿允许带错误存在，
    但用户必须能看到"为什么不能发布"，因此视图里始终给出逐节点错误列表。
    """
    return WorkflowView(id=row.id, name=row.name, description=row.description, domains=row.domains,
                        graph=row.graph, version=row.version, publishedRevision=row.published_revision,
                        status=row.status, validation=inspect_graph(FlowGraph.model_validate(row.graph)),
                        createTime=_utc_timestamp(row.created_at), updateTime=_utc_timestamp(row.updated_at))


def _device_starter_graph(domain: str, device_id: str) -> FlowGraph:
    """Create a useful first canvas with the entry device already fixed.

    中文补充：设备入口的初始画布：start -> 业务判定节点（alert 用 alert-evaluate、
    quality 用 qc-evaluate）-> end，且判定节点的 ``deviceId`` 已填成当前设备。
    只生成可编辑草稿，不含任何数据源或规则绑定，用户需要自行补全后发布。
    """
    middle_kind = "alert-evaluate" if domain == "alert" else "qc-evaluate"
    middle_title = "配置预警判断" if domain == "alert" else "配置质量标准"
    middle_config = editable_config(middle_kind)
    middle_config["deviceId"] = device_id
    return FlowGraph(
        nodes=[
            FlowNode(id="start", kind="start", title="开始", position=Position(x=80, y=180), config={}),
            FlowNode(
                id="business_check", kind=middle_kind, title=middle_title,
                position=Position(x=360, y=180), config=middle_config,
            ),
            FlowNode(id="end", kind="end", title="结束", position=Position(x=640, y=180), config={}),
        ],
        edges=[
            {"id": "edge_start_check", "source": "start", "target": "business_check", "port": "out"},
            {"id": "edge_check_end", "source": "business_check", "target": "end", "port": "out"},
        ],
    )


def _bind_existing_device_graph(ctx, workflow: Workflow, *, domain: str, device_id: str) -> None:
    """One-time compatibility repair for device drafts created before device locking.

    中文补充：早期从设备进入创建的工作流没有把 ``deviceId`` 写进节点配置，
    导致"设备工作流"其实可以跑任意设备的数据。这里做一次性修复：把该域下的业务节点
    统一补上设备标识，只有确有改动时才递增版本、追加修订并写审计（需要 workflows.edit）。
    """
    graph = deepcopy(workflow.graph)
    kinds = {"alert-evaluate"} if domain == "alert" else {"qc-appearance", "qc-component", "qc-evaluate"}
    changed = False
    for node in graph.get("nodes", []):
        if node.get("kind") not in kinds:
            continue
        config = node.setdefault("config", {})
        if config.get("deviceId") != device_id:
            config["deviceId"] = device_id
            changed = True
    if not changed:
        return
    require_permission(ctx, "workflows.edit")
    workflow.graph = graph
    workflow.version += 1
    flush_changes(ctx)
    add_revision(ctx, workflow, "固定设备入口")
    commit_change(ctx, "workflow.device_entry.bind", "workflows", workflow.id, {
        "deviceId": device_id, "domain": domain, "revision": workflow.version,
    })


def _binding_view(ctx, binding: DeviceWorkflowBinding, device, workflow: Workflow, *, created: bool) -> DeviceWorkflowEntryView:
    """组装"从设备打开工作流"的返回体（设备摘要 + 绑定 + 流程视图 + 站内跳转路径）。

    ``editorPath`` 按业务域指向 ``/alerts/...`` 或 ``/quality/...``；
    ``created`` 区分"本次新建草稿"与"打开已有绑定"，前端据此决定是否提示首次配置。
    """
    domain_path = "alerts" if binding.domain == "alert" else "quality"
    return DeviceWorkflowEntryView(
        bindingId=binding.id,
        device=equipment_registry.record_view(ctx, "devices", device),
        domain=binding.domain,
        provider=binding.provider,
        providerAppId=binding.provider_app_id,
        workflow=workflow_view(workflow),
        editorPath=f"/{domain_path}/devices/{device.id}/workflow",
        created=created,
    )


def ensure_device_workflow(ctx, payload: DeviceWorkflowEntryInput) -> DeviceWorkflowEntryView:
    """Resolve the one-click device entry without publishing or running anything.

    中文补充：``POST /device-entry``（``workflows_device_entry``）。语义是"读取或创建",
    绝不发布、运行或请求设备：设备必须已在对应业务域启用，已有绑定则直接返回（并做一次
    设备绑定修复），没有绑定才创建草稿（需要 ``workflows.create``）。

    并发处理：``(device, domain)`` 唯一约束可能被并发首次打开抢占，
    捕获 IntegrityError 后回滚并返回已提交的赢家，避免留下孤儿草稿。
    """
    require_permission(ctx, "workflows.view")
    require_permission(ctx, ("alerts" if payload.domain == "alert" else "quality") + ".view")
    device = equipment_registry.device(ctx, payload.device_id)
    if payload.domain not in device.domains:
        label = "设备预警" if payload.domain == "alert" else "质量检查"
        raise ValidationError(f"此设备尚未启用{label}用途，请先修改设备设置")

    binding = ctx.db.scalar(select(DeviceWorkflowBinding).where(
        DeviceWorkflowBinding.workspace_id == ctx.workspace_id,
        DeviceWorkflowBinding.device_id == device.id,
        DeviceWorkflowBinding.domain == payload.domain,
    ))
    if binding is not None:
        workflow = get_workflow(ctx, binding.workflow_id)
        _bind_existing_device_graph(ctx, workflow, domain=payload.domain, device_id=device.id)
        return _binding_view(ctx, binding, device, workflow, created=False)

    require_permission(ctx, "workflows.create")
    graph = _device_starter_graph(payload.domain, device.id)
    inspect_graph(graph)
    authorize_graph(ctx, graph)
    label = "预警" if payload.domain == "alert" else "质量检查"
    workflow = Workflow(
        workspace_id=ctx.workspace_id,
        owner_user_id=ctx.user_id,
        name=f"{device.name} · {label}",
        description=f"设备 {device.code} 的{label}工作流。节点配置、发布和运行均在此画布完成。",
        domains=[payload.domain],
        graph=graph.model_dump(by_alias=True, mode="json"),
        status="draft",
    )
    ctx.db.add(workflow)
    try:
        ctx.db.flush()
        binding = DeviceWorkflowBinding(
            workspace_id=ctx.workspace_id,
            owner_user_id=ctx.user_id,
            device_id=device.id,
            domain=payload.domain,
            workflow_id=workflow.id,
            provider="platform",
        )
        ctx.db.add(binding)
        ctx.db.flush()
    except IntegrityError:
        # A concurrent first open may win the unique device/domain binding.
        # Roll back the orphan draft and return the committed winner.
        ctx.db.rollback()
        binding = ctx.db.scalar(select(DeviceWorkflowBinding).where(
            DeviceWorkflowBinding.workspace_id == ctx.workspace_id,
            DeviceWorkflowBinding.device_id == device.id,
            DeviceWorkflowBinding.domain == payload.domain,
        ))
        if binding is None:
            raise
        workflow = get_workflow(ctx, binding.workflow_id)
        return _binding_view(ctx, binding, device, workflow, created=False)

    add_revision(ctx, workflow, "从设备直接创建")
    commit_change(ctx, "workflow.device_entry.create", "workflows", workflow.id, {
        "bindingId": binding.id, "deviceId": device.id, "domain": payload.domain,
    })
    return _binding_view(ctx, binding, device, workflow, created=True)


def get_workflow(ctx, workflow_id: str, permission: str = "workflows.view", *, deleted: bool = False) -> Workflow:
    """按权限取流程，并对其图中的资源做当前授权检查。

    ``permission`` 决定所需权限（view/edit/execute/publish/delete/export）；
    ``deleted=False`` 时把已软删除的流程当作"不存在"（对外不暴露删除痕迹），
    但历史查询、执行与恢复路径会传 ``deleted=True``，以便继续读取历史记录。
    异常：NotFoundError（不存在/已删除）、权限异常、ValidationError（图资源已撤销）。
    """
    row = owned_record(ctx, Workflow, workflow_id, permission)
    if row.status == "deleted" and not deleted:
        raise NotFoundError("工作流已移除")
    authorize_graph(ctx, FlowGraph.model_validate(row.graph))
    return row


def _domain_filter(ctx, domain: str):
    """Build a JSON-array membership predicate before authorization pagination.

    ``Workflow.domains.contains`` compiles to a substring search for the JSON
    type on SQLite/MySQL, so it misses rows whose array contains more than one
    value.  Use each backend's native JSON membership operation instead.  The
    platform store currently supports MySQL and SQLite; the fallback keeps the
    query usable on other SQLAlchemy dialects while preserving the optional
    filter contract.
    """
    dialect = ""
    try:
        dialect = ctx.db.get_bind().dialect.name
    except Exception:
        # Unit tests and migration tooling may provide a query-like session
        # without a bound engine; the generic expression remains valid there.
        pass
    if dialect == "mysql":
        return func.JSON_CONTAINS(Workflow.domains, json.dumps(domain, ensure_ascii=False)) == 1
    if dialect == "sqlite":
        values = func.json_each(Workflow.domains).table_valued("value").alias("workflow_domain")
        return exists(select(1).select_from(values).where(values.c.value == domain))
    return Workflow.domains.contains([domain])


def list_workflows(ctx, *, limit: int = 50, offset: int = 0, keyword: str = "", domain: str | None = None) -> list[WorkflowView]:
    """``GET /api/v1/workflows``：按权限分页列出流程（不含已删除）。

    分页策略：先在 SQL 侧做关键词/业务域/排序，再按批（每批 min(100, max(30, limit)) 行）
    取出并逐条做资源授权，只对通过授权的行计数，凑满 limit 即返回。
    这样既不会把设备目录里成千上万的图全部加载到内存，也不会因"最后一条无权"而返回空页。
    业务域过滤必须发生在 SQL 侧（见 ``_domain_filter`` 的注释），否则过滤后可能得到误导性的空页。
    """
    query = visible_records(ctx, Workflow, "workflows.view").where(Workflow.status != "deleted")
    if keyword:
        query = query.where(Workflow.name.contains(keyword, autoescape=True))
    if domain:
        # Apply this predicate to the SQL query before the authorization-aware
        # paging loop; filtering only the current page would hide valid rows
        # and could expose a misleading empty page.
        query = query.where(_domain_filter(ctx, domain))
    # Filter authorization before pagination, so inaccessible rows don't disclose counts.
    query = query.order_by(Workflow.updated_at.desc(), Workflow.id)
    visible, scanned, accepted = [], 0, 0
    while len(visible) < limit:
        # A device catalogue can contain thousands of graphs. Read only enough
        # rows to fill the requested authorized page, instead of loading all.
        rows = ctx.db.scalars(query.limit(min(100, max(30, limit))).offset(scanned)).all()
        if not rows:
            break
        scanned += len(rows)
        for row in rows:
            try:
                authorize_graph(ctx, FlowGraph.model_validate(row.graph))
            except (NotFoundError, PermissionErrorEx):
                continue
            if accepted >= offset:
                visible.append(workflow_view(row))
                if len(visible) == limit:
                    break
            accepted += 1
    return visible


def snapshot(row: Workflow) -> dict:
    """导出/修订共用的快照结构：只含名称、说明、业务域与图。

    刻意不含运行输入、源凭据与历史结果；``add_revision`` 会在其上追加 ``published`` 标记。
    """
    return {"name": row.name, "description": row.description, "domains": row.domains, "graph": row.graph}


def add_revision(ctx, row: Workflow, reason: str) -> None:
    """追加一条不可变修订（用当前 ``row.version`` 作为修订号）。

    调用前必须已经递增 ``row.version`` 并 flush，否则修订号会与旧记录冲突
    （``(workflow_id, revision)`` 唯一约束）。``published`` 标记表示该修订就是当前正式版本。
    """
    ctx.db.add(WorkflowRevision(workspace_id=ctx.workspace_id, owner_user_id=row.owner_user_id,
                                workflow_id=row.id, revision=row.version,
                                snapshot={**snapshot(row), "published": row.status == "published" and row.published_revision == row.version},
                                reason=reason))


def save_workflow(ctx, payload: WorkflowInput, workflow_id: str | None = None) -> WorkflowView:
    """创建或更新草稿（``POST /`` 需要 workflows.create，``PUT /{id}`` 需要 workflows.edit）。

    先做图结构与资源授权校验，再写库：``workflow_id`` 为空表示新建；
    否则要求 ``payload.version`` 与当前一致（乐观锁，过期报 409）。
    业务域去重后保存；保存只改草稿并追加修订，**不会发布也不会影响正在使用的发布版本**。
    副作用：写 workflow_definitions、workflow_revisions 与审计。
    """
    inspect_graph(payload.graph)
    authorize_graph(ctx, payload.graph)
    if workflow_id:
        row = get_workflow(ctx, workflow_id, "workflows.edit")
        check_version(row, payload.version)
        row.version += 1
    else:
        require_permission(ctx, "workflows.create")
        row = Workflow(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, status="draft")
        ctx.db.add(row)
    row.name, row.description = payload.name, payload.description
    row.domains = list(dict.fromkeys(payload.domains))
    row.graph = payload.graph.model_dump(by_alias=True, mode="json")
    flush_changes(ctx)
    add_revision(ctx, row, "编辑草稿" if workflow_id else "创建流程")
    commit_change(ctx, "workflow.save", "workflows", row.id, {"revision": row.version})
    return workflow_view(row)


def published_revision_view(ctx, workflow_id: str) -> dict:
    """返回正式运行的不可变图和当前提交版本，防止草稿参数混入已发布运行。"""
    row = get_workflow(ctx, workflow_id)
    if row.status != "published" or row.published_revision is None:
        raise ValidationError("流程尚未发布或已停用，不能读取正式运行契约")
    selected = revision(ctx, row, row.published_revision)
    return {"revision": selected.revision, "version": row.version, "graph": selected.snapshot["graph"],
            "name": selected.snapshot.get("name") or row.name, "domains": selected.snapshot.get("domains") or row.domains}


def revision(ctx, row: Workflow, revision_number: int) -> WorkflowRevision:
    """取指定修订快照，并按**当前**权限重新校验快照里的资源引用。

    这是"历史结果也要受权限保护"的落点：数据源被撤销后，旧修订的图不可读。
    异常：NotFoundError（修订不存在）、权限异常。
    """
    record = ctx.db.scalar(select(WorkflowRevision).where(WorkflowRevision.workspace_id == ctx.workspace_id,
                           WorkflowRevision.workflow_id == row.id, WorkflowRevision.revision == revision_number))
    if record is None:
        raise NotFoundError("流程修订不存在")
    authorize_graph(ctx, FlowGraph.model_validate(record.snapshot["graph"]))
    return record


def change_status(ctx, workflow_id: str, version: int, *, publish: bool) -> WorkflowView:
    """发布或停用流程（``POST /{id}/publish`` 与 ``/{id}/disable``，需要 workflows.publish）。

    发布时先 ``executable_graph``（结构 + active 资源授权），通过后把
    ``published_revision`` 指向新版本并置 status=published；停用只置 status=disabled，
    停止后续正式运行，**不宣称中断已经开始的外部请求**。
    两者都会递增 version、追加修订并写审计；异常：ValidationError（图不可执行）、版本冲突。
    """
    row = get_workflow(ctx, workflow_id, "workflows.publish")
    check_version(row, version)
    if publish:
        executable_graph(ctx, FlowGraph.model_validate(row.graph))
    row.version += 1
    if publish:
        row.published_revision = row.version
        row.status = "published"
    else:
        row.status = "disabled"
    flush_changes(ctx)
    add_revision(ctx, row, "发布版本" if publish else "停用流程")
    commit_change(ctx, "workflow.publish" if publish else "workflow.disable", "workflows", row.id)
    return workflow_view(row)


def restore(ctx, workflow_id: str, version: int, revision_number: int) -> WorkflowView:
    """把历史修订恢复成新草稿（``POST /{id}/restore``，需要 workflows.edit）。

    复制指定修订的名称/说明/业务域/图，``version += 1`` 后追加一条"恢复修订 N"的新记录；
    当前 ``published_revision`` 与正式运行版本保持不变，历史修订本身也不会被改写。
    异常：NotFoundError（修订不存在）、版本冲突、权限异常。
    """
    row = get_workflow(ctx, workflow_id, "workflows.edit")
    check_version(row, version)
    record = revision(ctx, row, revision_number)
    # Restoration creates a new draft but leaves the currently published revision pinned.
    row.name, row.description, row.domains, row.graph = (record.snapshot[key] for key in ("name", "description", "domains", "graph"))
    row.version += 1
    flush_changes(ctx)
    add_revision(ctx, row, "恢复修订 " + str(revision_number))
    commit_change(ctx, "workflow.restore", "workflows", row.id, {"revision": revision_number})
    return workflow_view(row)


def list_revisions(ctx, workflow_id: str) -> list[dict]:
    """列出最近 100 条修订（``GET /{id}/revisions``，需要 workflows.view）。

    逐条校验当前资源授权：无权读取的修订只返回 ``available=False`` 且 snapshot 为 None，
    既告知历史存在，又不泄露其内容。返回项含 revision/reason/createTime/available/snapshot。
    """
    row = get_workflow(ctx, workflow_id)
    records = ctx.db.scalars(select(WorkflowRevision).where(WorkflowRevision.workspace_id == ctx.workspace_id,
                            WorkflowRevision.workflow_id == row.id).order_by(WorkflowRevision.revision.desc()).limit(100)).all()
    output = []
    for record in records:
        available = True
        try:
            authorize_graph(ctx, FlowGraph.model_validate(record.snapshot["graph"]))
        except (NotFoundError, PermissionErrorEx):
            available = False
        output.append({"revision": record.revision, "reason": record.reason, "createTime": _utc_timestamp(record.created_at),
                       "available": available, "snapshot": record.snapshot if available else None})
    return output


def remove(ctx, workflow_id: str, version: int) -> None:
    """软删除流程（``DELETE /{id}``，需要 workflows.delete 并带当前 version）。

    只把 status 置为 deleted：修订、设备绑定与全部执行历史都保留，供追溯与历史报告读取；
    也不会删除任何外部数据（数据源、目标表）。异常：版本冲突、权限异常。
    """
    row = get_workflow(ctx, workflow_id, "workflows.delete")
    check_version(row, version)
    row.status, row.version = "deleted", row.version + 1
    flush_changes(ctx)
    add_revision(ctx, row, "移除（保留运行和历史）")
    commit_change(ctx, "workflow.delete", "workflows", row.id)
