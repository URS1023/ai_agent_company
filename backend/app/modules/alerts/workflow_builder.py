"""Build and atomically publish a ready equipment alert workflow from one normal form.

职责：设备预警"普通业务向导"的后端 —— 用户只填一张表单（策略修订、测点、可选计算、
可选通知/写回、可选调度），本模块把它编译成一张完整可执行的流程画布，并**一次性**
发布：流程 + 首修订 + 调度任务 + 幂等回执 + 审计，全部在同一事务里完成。

位置与依赖：只被 ``alerts.router`` 的向导接口调用；向下依赖 ``workflows``（图校验、
修订快照、发布）、``analytics``（源可见性、SQL 只读授权、元数据）、``collection``（HTTP 源
配置）、``system_tools``（调度任务与下次执行时间）。

对外语义与约束：
- 权限：创建需 ``alerts.view`` / ``alerts.publish`` / ``workflows.view`` /
  ``workflows.create`` / ``workflows.publish``；带调度时另需 ``system.operations.manage``
  与 ``workflows.execute`` / ``alerts.execute``。
- 幂等：以 ``request_key``（客户端防重复键）标识一次向导提交，用 ``request_hash``
  校验"同键必须同配置"；重复提交返回既有结果（``reused=True``），配置不同或流程已被改动则 409。
- 预检：所有引用（策略修订、设备与用途、测点、数据源与版本、字段引用、SQL 授权、图可执行性）
  在写库前全部校验，因此本方法要么全成、要么不留半成品。
- 画布是"数据流"形态：start → collect → extract →(calculate)→ evaluate →(notify)→(write_result)→ end，
  节点标题与坐标固定，前端据此做只读预览。
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from sqlalchemy import select

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.common.owned_records import owned_record
from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import DataSource
from app.modules.alerts.models import AlertWorkflowCreation, MeasurementPoint
from app.modules.alerts.registry import device, get_record, rule_revision
from app.modules.alerts.workflow_schemas import AlertWorkflowInput, AlertWorkflowView
from app.modules.analytics.connections import database_name, source_version
from app.modules.analytics.metadata import source_metadata
from app.modules.analytics.sources import get_source
from app.modules.analytics.sql_safety import authorize_sql
from app.modules.collection.schemas import ExtractField
from app.modules.collection.sources import http_source, source_config
from app.modules.system.service_helpers import commit_change, flush_changes
from app.modules.system_tools.models import ScheduledJob
from app.modules.system_tools.schedules import job_view, next_run
from app.modules.workflows.expressions import formula_expression, validate_expression
from app.modules.workflows.graph import authorize_graph, executable_graph
from app.modules.workflows.models import Workflow
from app.modules.workflows.schemas import FlowGraph, RunInput
from app.modules.workflows.service import add_revision, get_workflow, workflow_view


def _request_hash(payload: AlertWorkflowInput) -> str:
    """计算"除防重复键外"的配置摘要，用于识别同键不同配置的重复提交。

    排序键 + 紧凑分隔符 + ``allow_nan=False``，保证同一份配置在任何进程/时机都得到同一摘要；
    因此字段顺序或空格变化不会误判为不同配置，而任何业务字段变化都会被检出（409）。
    """
    document = payload.model_dump(by_alias=True, mode="json", exclude={"request_key"})
    # 未确认时保留旧客户端的摘要口径；明确授权必须进入摘要，不能复用未授权的创建回执。
    if not payload.confirm_http_writes:
        document.pop("confirmHttpWrites", None)
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(encoded.encode()).hexdigest()


def _node(node_id: str, kind: str, title: str, x: int, *, config: dict | None = None) -> dict:
    """构造一个画布节点（y 固定 100，x 由调用方按展示顺序给定）。

    ``title`` 截断到 160 字符以符合图节点契约；``kind`` 必须是工作流引擎已注册的节点类型。
    """
    return {
        "id": node_id, "kind": kind, "title": title[:160], "position": {"x": x, "y": 100},
        "inputs": {}, "config": config or {},
    }


def _edge(source: str, target: str) -> dict:
    """构造一条顺序边；边 id 由两端节点 id 拼成，故同一对节点不能出现两条边。"""
    return {"id": f"edge_{source}_{target}", "source": source, "target": target, "port": "out"}


def _field_references(expression: Any) -> set[str]:
    """递归收集规则/公式表达式里引用的字段名。

    支持两种形式：``{"field": "x"}`` 单字段引用，以及带 ``args`` 的复合表达式。
    不是字典的（如字面量、None）返回空集 —— 校验采用该返回值做"是否提供了字段"的判定。
    """
    if not isinstance(expression, dict):
        return set()
    if set(expression) == {"field"} and isinstance(expression["field"], str):
        return {expression["field"]}
    references: set[str] = set()
    for child in expression.get("args") or []:
        references |= _field_references(child)
    return references


def _point_type(value: str) -> str:
    """把测点类型映射为 JSONPath 提取节点的字段类型。

    未识别的类型统一落到 ``auto``（由提取节点自行判断），不要在这里抛错 ——
    测点类型是用户自定义文本，兼容历史数据比严格枚举更重要。
    """
    return {
        "number": "decimal", "decimal": "decimal", "float": "decimal",
        "integer": "integer", "int": "integer", "boolean": "boolean", "bool": "boolean",
        "text": "string", "string": "string", "json": "json",
    }.get(value.lower(), "auto")


def _selected_points(ctx: AuthContext, device_id: str, point_ids: list[str]) -> list[MeasurementPoint]:
    """按 id 顺序取出所选测点，并做三重校验后保证顺序即表单顺序。

    校验：测点必须属于该设备、必须处于 ``active``、映射的采集字段名不得重复
    （同名会导致提取节点字段覆盖，故直接拒绝）。任一不满足抛 ``ValidationError``；
    同时会通过 ``get_record`` 复核测点的可见权限。
    """
    rows: list[MeasurementPoint] = []
    for point_id in point_ids:
        row = get_record(ctx, "points", point_id)
        if row.device_id != device_id:
            raise ValidationError("所选测点不属于当前策略设备", point_id=point_id)
        if row.status != "active":
            raise ValidationError("所选测点已停用，不能发布自动预警", point_id=point_id)
        rows.append(row)
    names = [row.field_name for row in rows]
    if len(set(names)) != len(names):
        raise ValidationError("所选测点映射了重复采集字段，请保留一个或修正测点配置")
    return rows


def _validate_fields(payload: AlertWorkflowInput, snapshot: dict, points: list[MeasurementPoint]) -> set[str]:
    """校验计算字段与策略字段的引用闭合，返回"必须由采集直接提供"的原始字段集合。

    规则：按表单顺序逐个计算项校验（表达式合法、引用的字段必须来自测点或**此前**的计算项、
    输出名不得重复），再做策略侧引用校验（规则表达式字段 + ``recordKeyField``）。
    返回值用于标记 JSONPath 提取字段的 ``required``：只有原始字段才置必填，
    计算产生的字段不该出现在提取节点里，否则会在采集阶段误判为缺字段。
    缺字段或重名时抛 ``ValidationError`` 并带上缺失字段列表，便于前端定位。
    """
    available = {row.field_name for row in points}
    raw_required: set[str] = set()
    calculation_names: set[str] = set()
    for item in payload.calculations:
        if item.name in calculation_names:
            raise ValidationError("计算输出字段名不能重复", field=item.name)
        expression = formula_expression(item.formula) if item.formula is not None else item.expression
        validate_expression(expression)
        required = _field_references(expression)
        missing = required - available
        if missing:
            raise ValidationError("计算公式引用了尚未选择或尚未生成的字段", fields=sorted(missing), calculation=item.name)
        raw_required |= required - calculation_names
        available.add(item.name)
        calculation_names.add(item.name)
    policy_fields = set()
    for rule in snapshot["rules"]:
        policy_fields |= _field_references(rule["expression"])
    if snapshot.get("recordKeyField"):
        policy_fields.add(snapshot["recordKeyField"])
    missing = policy_fields - available
    if missing:
        raise ValidationError("预警策略引用的字段未由所选测点或计算字段提供", fields=sorted(missing))
    raw_required |= policy_fields - calculation_names
    return raw_required


def _source_node(ctx: AuthContext, payload: AlertWorkflowInput, equipment, points: list[MeasurementPoint]) -> tuple[dict, str]:
    """构造采集节点，并返回该节点与"来源类型"中文标签。

    两条分支（互斥，由设备绑定源的 provider 决定）：
    - ``http``：要求数据源已允许工作流自动采集、且不得携带 SQL 参数；测点字段必须落在
      该源已配置的 JSONPath 输出字段内（未配置字段时不额外限制）。节点为 ``http-request``。
    - 数据库（mysql/postgres/sqlite）：必须给出显式只读 SELECT，另需
      ``datasources.execute`` 权限，并用源码元数据做 ``authorize_sql`` 只读校验；
      ``maxRows`` 取"表单值"与"数据源配置上限"的较小者。节点为 ``database-query``。

    校验失败一律抛 ``ValidationError``；数据源版本与表单不一致抛 ``ConflictError``
    （并发编辑提示刷新）。副作用：仅读数据源配置与元数据，不执行用户 SQL
    （真正执行在流程运行时），审计字段据此记录 ``database_metadata_only``。
    """
    if not equipment.datasource_id:
        raise ValidationError("设备尚未绑定采集数据源，请先在设备台账选择 HTTP 或数据库来源")
    if payload.source.datasource_id != equipment.datasource_id:
        raise ValidationError("提交的数据源与设备当前绑定来源不一致，请刷新后重新核对")
    source = owned_record(ctx, DataSource, equipment.datasource_id, "datasources.view")
    if source.status != "active":
        raise ValidationError("设备采集数据源已停用，不能发布自动预警")
    if source_version(source) != payload.source.datasource_version:
        raise ConflictError("数据源配置版本已变化，请重新核对请求体、字段或 SQL", current_version=source_version(source))
    fields = {row.field_name for row in points}
    if source.provider == "http":
        source = http_source(ctx, source.id, active=True)
        config = source_config(source)
        if not config.allow_automatic:
            raise ValidationError("HTTP 数据源尚未允许工作流自动采集")
        # 自动采集只说明来源可供流程使用，非 GET 的外部副作用仍需单独明确确认。
        needs_write_confirmation = config.method != "GET"
        if needs_write_confirmation and not payload.confirm_http_writes:
            raise ValidationError("HTTP 非 GET 请求可能产生外部副作用，请明确确认后创建预警流程")
        if payload.source.query or payload.source.parameters:
            raise ValidationError("HTTP 数据源不接受 SQL 或 SQL 参数")
        # 只比较"已配置字段"与测点字段的包含关系；未配置字段的源不做限制。
        configured = {item.name for item in config.fields}
        if configured and not fields <= configured:
            raise ValidationError("所选测点不在 HTTP 数据源已配置的 JSONPath 输出字段中", fields=sorted(fields - configured))
        return _node("collect", "http-request", "HTTP 单条/批量采集", 320, config={
            "datasourceId": source.id, "datasourceVersion": source_version(source),
            **({"confirmWrites": True} if needs_write_confirmation else {}),
        }), "HTTP"

    source = get_source(ctx, source.id, active=True)
    if source.provider not in {"mysql", "postgres", "sqlite"}:
        raise ValidationError("设备预警数据库采集仅支持 MySQL、PostgreSQL 或受管 SQLite")
    if not payload.source.query:
        raise ValidationError("数据库来源必须填写明确的只读 SELECT 查询")
    require_permission(ctx, "datasources.execute")
    metadata = source_metadata(ctx, source)
    # 以数据源自身行数上限收敛表单值，防止向导绕过数据源治理设置。
    maximum = min(payload.source.max_rows, int((source.connection_config or {}).get("maxRows") or 1000))
    authorize_sql(
        payload.source.query, provider=source.provider, tables=metadata.tables_json,
        max_rows=maximum, parameters=payload.source.parameters, database=database_name(source),
    )
    return _node("collect", "database-query", "数据库只读采集", 320, config={
        "datasourceId": source.id, "datasourceVersion": source_version(source),
        "query": payload.source.query, "parameters": payload.source.parameters, "maxRows": maximum,
    }), "数据库"


def _build_graph(ctx: AuthContext, payload: AlertWorkflowInput) -> tuple[FlowGraph, list[str]]:
    """按表单编译画布，返回 ``(图, 已选字段名列表)``。

    顺序：校验策略修订/设备 → 校验测点 → 校验字段引用 → 校验数据源 → 组装节点与边
    → ``FlowGraph`` 结构校验 → ``executable_graph`` 校验（引用资源仍可执行）。
    带调度时额外做 ``authorize_graph(..., execute=True)``：调度会在未来无人值守地运行，
    因此必须此刻就确认所有资源的**执行**权限，而不是只校验查看权限。

    节点可选性：计算节点仅在有计算项时插入；通知/写回节点仅当表单给出时插入；
    结束节点恒存在，负责"保存报告与问题"。坐标按是否插入计算节点整体右移，避免重叠。
    """
    selected_revision = rule_revision(ctx, payload.rule_id, payload.rule_revision, active=True)
    snapshot = selected_revision.snapshot
    equipment = device(ctx, snapshot["deviceId"], active=True)
    points = _selected_points(ctx, equipment.id, payload.point_ids)
    raw_required = _validate_fields(payload, snapshot, points)
    source_node, source_label = _source_node(ctx, payload, equipment, points)

    extraction_fields = [ExtractField(
        name=row.field_name,
        # path 必须与提取节点约定一致（$.data[*] 内的字段名），字段名做 JSON 转义以支持特殊字符。
        path="$[" + json.dumps(row.field_name, ensure_ascii=False) + "]",
        type=_point_type(row.point_type),
        required=row.field_name in raw_required,
        comment=(row.name + (("（" + row.unit + "）") if row.unit else ""))[:500],
    ).model_dump(mode="json") for row in points]
    nodes = [
        _node("start", "start", "开始 · 计划采集", 40),
        source_node,
        _node("extract", "jsonpath-extract", "提取已选测点字段", 600, config={
            "recordPath": "$.data", "fields": extraction_fields,
        }),
    ]
    edges = [_edge("start", "collect"), _edge("collect", "extract")]
    previous = "extract"
    if payload.calculations:
        nodes.append(_node("calculate", "arithmetic", "执行测点计算", 880, config={
            "fields": [item.model_dump(by_alias=True, mode="json") for item in payload.calculations],
        }))
        edges.append(_edge(previous, "calculate"))
        previous = "calculate"
    alert_x = 1160 if payload.calculations else 880
    # 判定节点固定绑定"策略 + 修订号"，因此后续再次编辑策略不会改变已发布流程的判定口径。
    nodes.append(_node("evaluate", "alert-evaluate", snapshot["name"] + " · 规则判定", alert_x, config={
        "ruleId": selected_revision.rule_id, "ruleRevision": selected_revision.revision,
    }))
    edges.append(_edge(previous, "evaluate"))
    previous = "evaluate"
    x = alert_x + 280
    if payload.notification is not None:
        nodes.append(_node("notify", "alert-notify", "发送站内预警", x,
                           config=payload.notification.model_dump(by_alias=True, mode="json")))
        edges.append(_edge(previous, "notify"))
        previous, x = "notify", x + 280
    if payload.writeback is not None:
        nodes.append(_node("write_result", "output-table", "写入授权目标表", x,
                           config=payload.writeback.model_dump(by_alias=True, mode="json")))
        edges.append(_edge(previous, "write_result"))
        previous, x = "write_result", x + 280
    nodes.append(_node("end", "end", "完成 · 保存报告与问题", x))
    edges.append(_edge(previous, "end"))
    graph = FlowGraph.model_validate({"nodes": nodes, "edges": edges})
    executable_graph(ctx, graph)
    if payload.schedule is not None:
        authorize_graph(ctx, graph, active=True, execute=True)
    return graph, [row.field_name for row in points]


def _schedule_payload(ctx: AuthContext, payload: AlertWorkflowInput, workflow_id: str) -> ScheduledJob | None:
    """构造调度任务行（未 flush），无调度表单时返回 ``None``。

    权限：``system.operations.manage``（调度属系统运维能力）以及
    ``workflows.execute`` / ``alerts.execute``（无人值守执行需要的执行权）。
    ``code`` 由 ``工作区:用户:防重复键`` 派生并哈希，保证同一向导请求重放时得到同一任务编码；
    ``next_run_at`` 只在 ``status == "active"`` 时计算，停用任务不排期。

    **``run_payload`` 中 version 相关字段的取值语义（重点，易踩坑）**：
    - ``"version": 1``：这是 ``RunInput.version``，即"保存时当前编辑版本"（乐观锁版本），
      不是发布修订。这里**硬编码为 1**，只因为流程紧接着由向导自己创建、
      ``Workflow.version`` 恰好是 1。它是一次性占位值：调度真正执行时
      ``system_tools.schedules._dispatch`` 会用 `当时的 `workflow.version` 覆盖它
      （注释明确写着"编辑版本与不可变发布修订是两个契约，只刷新前者"），
      所以它**不构成版本绑定**，也不需要随流程版本自增而更新。
    - ``"publishedRevision": 1``：这才是**真正固定"跑哪一版图"**的字段，同样硬编码为 1。
      它与 ``Workflow.published_revision`` 在向导创建时一致（创建即 ``version=1`` /
      ``published_revision=1``，``add_revision`` 直接用未递增的 version 作为首修订号），
      但**流程版本会随后续编辑/再次发布自增**（``add_revision`` 以 `row.version` 为修订号）。
      于是存在已知冲突：一旦该流程被改到修订 2 并重新发布，此调度仍绑定修订 1，
      会继续执行旧的已发布图且不会自动跟随新修订；要改绑必须显式更新调度
      （``_published_binding`` 会拒绝不存在或未发布的修订，因此也不能随手改成任意数字）。
    - ``AlertWorkflowCreation.published_revision`` 与 ``create_published`` 里写入的
      ``Workflow.version/published_revision=1`` 也是同一口径的硬编码 1，
      三者必须保持一致：改动任一处（例如让向导支持"基于已有流程再加调度"）都要同步改，
      否则幂等回执的"流程后来是否被改动"校验会误报 409。
    """
    if payload.schedule is None:
        return None
    require_permission(ctx, "system.operations.manage")
    for permission in ("workflows.execute", "alerts.execute"):
        require_permission(ctx, permission)
    schedule = payload.schedule
    calculated = next_run(schedule.schedule_kind, schedule.schedule_expression, schedule.timezone)
    run_payload = {
        "version": 1, "mode": "published", "inputs": {},
        "confirmWrites": payload.writeback is not None, "timeoutSeconds": 120,
    }
    RunInput.model_validate({**run_payload, "idempotencyKey": "schedule-validation"})
    # 固定首修订（见上方 docstring：与 workflow 版本自增存在已知冲突）。
    run_payload["publishedRevision"] = 1
    code_seed = f"{ctx.workspace_id}:{ctx.user_id}:{payload.request_key}"
    code = "alert." + sha256(code_seed.encode()).hexdigest()[:24]
    return ScheduledJob(
        workspace_id=ctx.workspace_id, created_by=ctx.user_id, code=code,
        # 名称/描述按表中列宽截断（200/1000），避免超长写入失败。
        name=(payload.name + " · 自动执行")[:200],
        description=("由设备预警向导创建，固定已发布流程修订；" + payload.description)[:1000],
        task_type="alert", target_id=workflow_id,
        schedule_kind=schedule.schedule_kind, schedule_expression=schedule.schedule_expression,
        timezone=schedule.timezone, payload=run_payload, status=schedule.status,
        next_run_at=calculated if schedule.status == "active" else None,
    )


def _selected_fields(row: Workflow) -> list[str]:
    """从已保存画布里回读 JSONPath 提取节点的字段名，供幂等重放时返回相同视图。

    约定：只认 ``jsonpath-extract`` 节点的 ``config.fields[*].name``，与 ``_build_graph``
    生成的图对应；若图的形态被其他入口改动（例如手工编辑流程），这里返回的集合可能不同，
    仅用于展示，不参与校验。
    """
    graph = FlowGraph.model_validate(row.graph)
    extract = next((item for item in graph.nodes if item.kind == "jsonpath-extract"), None)
    return [str(item.get("name")) for item in (extract.config.get("fields") if extract else []) or []]


def create_published(ctx: AuthContext, payload: AlertWorkflowInput) -> AlertWorkflowView:
    """Preflight all references and atomically save workflow, revision, schedule, receipt and audit.

    （中文补充）向导的唯一写入口：``POST`` 语义，需 ``alerts.view`` / ``alerts.publish`` /
    ``workflows.view`` / ``workflows.create`` / ``workflows.publish``。
    执行顺序：
    1) 用 ``(工作区, 用户, request_key)`` 查幂等回执 —— 命中且摘要一致、流程仍是当时发布的
       那一修订时，直接返回既有结果（``reused=True``，不重复建流程/调度）；
       摘要不同抛 ``ConflictError``；流程后来被改过也抛 ``ConflictError``（提示直接打开既有流程）。
    2) 未命中则预检并编译画布（``_build_graph``），创建 ``status='published'``、
       ``version=1`` / ``published_revision=1`` 的流程，追加首修订（reason=
       "设备预警向导创建并发布"，与调度绑定校验的历史发布原因保持一致）。
    3) 可选创建调度任务，最后写回执并落审计（``alert.workflow.publish``），
       审计里记录"创建期是否读过源数据"（仅数据库元数据 / 未读）。
    返回 ``AlertWorkflowView``（流程视图 + 可选调度视图 + 已选字段 + ``reused``）。
    副作用：写 workflow_definitions / workflow_revisions / system_scheduled_jobs /
    equipment_alert_workflow_creations 并提交事务；任一步失败整体回滚，不留半成品。
    """
    for permission in ("alerts.view", "alerts.publish", "workflows.view", "workflows.create", "workflows.publish"):
        require_permission(ctx, permission)
    digest = _request_hash(payload)
    receipt = ctx.db.scalar(select(AlertWorkflowCreation).where(
        AlertWorkflowCreation.workspace_id == ctx.workspace_id,
        AlertWorkflowCreation.owner_user_id == ctx.user_id,
        AlertWorkflowCreation.request_key == payload.request_key,
    ))
    if receipt is not None:
        if receipt.request_hash != digest:
            raise ConflictError("此防重复键已用于另一份预警流程配置，请核对既有结果", workflow_id=receipt.workflow_id)
        row = get_workflow(ctx, receipt.workflow_id)
        if row.status != "published" or row.published_revision != receipt.published_revision:
            raise ConflictError("此请求已创建流程，但流程后来已修改或停用，请直接打开既有流程核对", workflow_id=row.id)
        scheduled = ctx.db.get(ScheduledJob, receipt.schedule_id) if receipt.schedule_id else None
        if receipt.schedule_id and (scheduled is None or scheduled.workspace_id != ctx.workspace_id):
            raise NotFoundError("预警流程的调度记录不存在")
        return AlertWorkflowView(
            workflow=workflow_view(row), schedule=job_view(scheduled) if scheduled else None,
            selectedFields=_selected_fields(row), reused=True,
        )

    graph, fields = _build_graph(ctx, payload)
    row = Workflow(
        workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, version=1,
        name=payload.name, description=payload.description, domains=["alert"],
        graph=graph.model_dump(by_alias=True, mode="json"), status="published", published_revision=1,
    )
    ctx.db.add(row)
    flush_changes(ctx)
    add_revision(ctx, row, "设备预警向导创建并发布")
    scheduled = _schedule_payload(ctx, payload, row.id)
    if scheduled is not None:
        ctx.db.add(scheduled)
        flush_changes(ctx)
    ctx.db.add(AlertWorkflowCreation(
        workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        workflow_id=row.id, schedule_id=scheduled.id if scheduled else None,
        request_key=payload.request_key, request_hash=digest, published_revision=1,
    ))
    flush_changes(ctx)
    commit_change(ctx, "alert.workflow.publish", "workflows", row.id, {
        "revision": 1, "ruleId": payload.rule_id, "ruleRevision": payload.rule_revision,
        "sourceId": payload.source.datasource_id, "selectedFields": fields,
        "calculationCount": len(payload.calculations), "notification": payload.notification is not None,
        "writeback": payload.writeback is not None, "scheduleId": scheduled.id if scheduled else None,
        "sourceReadDuringCreation": "database_metadata_only" if payload.source.query else "none",
    })
    return AlertWorkflowView(
        workflow=workflow_view(row), schedule=job_view(scheduled) if scheduled else None,
        selectedFields=fields, reused=False,
    )
