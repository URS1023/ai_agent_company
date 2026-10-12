"""Business Bot configuration, grounded debugging, publication and hashed API keys."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.common.audit import audit
from app.common.authorization import access_policy, require_permission, require_resource
from app.common.data_scope import bind_data_scope
from app.common.deps import AuthContext
from app.common.owned_records import owned_record, visible_records
from app.errors import AuthError, ConflictError, ModelError, NotFoundError, ValidationError
from app.models import (
    Connector, ConnectorVersion, File, KnowledgeBase, KnowledgeChunk,
    KnowledgeDocument, Skill, SkillVersion, Tool, ToolVersion,
)
from app.modules.knowledge_graph import graphs as graph_service
from app.modules.knowledge_graph.models import (
    BotApiKey,
    BotDebugRun,
    BotRevision,
    BusinessBot,
    GraphEntity,
    GraphRelation,
    GraphRevision,
    KnowledgeGraph,
)
from app.modules.knowledge_graph.schemas import BotApiKeyInput, BotDebugInput, BotInput, PublishInput
from app.modules.workflows import service as workflow_service
from app.modules.workflows.models import Workflow
from app.providers import model
from app.providers.tool.base import ToolSpec
from app.providers.tool.gateway import ToolGateway, build_context


def _iso(value: datetime | None) -> str | None:
    """时间转 ISO 字符串；None 原样返回 None（前端按"未设置"处理，不显示 1970）。"""
    return value.isoformat() if value else None


def _version(record, expected: int | None, *, creating: bool = False) -> None:
    """全模块统一的乐观锁校验：新建不允许带 version，修改必须带且必须与服务端一致。

    creating=False 时 expected 为 None 会报错，是为了防"忘记传 version 导致静默覆盖"；
    creating=True 时传了 version 也报错，是为了让前端明确区分新建与修改两条路径。
    异常：ValidationError（version 缺失或不该出现）、ConflictError（版本不一致，带 current_version）。
    """
    if creating:
        if expected is not None:
            raise ValidationError("新建记录不能提交 version")
        return
    if expected is None:
        raise ValidationError("修改记录必须提交 version")
    if record.version != expected:
        raise ConflictError("记录已被其他操作修改，请刷新后重试", current_version=record.version)


def _commit(ctx: AuthContext, message: str) -> None:
    """提交事务并把两类数据库异常翻译成业务冲突。

    IntegrityError（唯一约束冲突，例如同名 Bot）与 StaleDataError（SQLAlchemy 版本列检测到并发
    修改）都回滚后抛 ConflictError，让调用方拿到 409 而不是 500——这两种都是"用户刷新重试即可"的情况。
    参数 message 是给用户看的冲突文案（仅用于 IntegrityError；StaleDataError 用统一文案）。
    """
    try:
        ctx.db.commit()
    except IntegrityError as exc:
        ctx.db.rollback()
        raise ConflictError(message) from exc
    except StaleDataError as exc:
        ctx.db.rollback()
        raise ConflictError("记录已被其他操作修改，请刷新后重试") from exc


def get_bot(ctx: AuthContext, bot_id: str, permission: str = "bots.view") -> BusinessBot:
    """取 Bot 并做归属/权限校验（owned_record 同时管好工作区隔离与资源级授权）。

    已软删除（status=="deleted"）的 Bot 视为不存在。
    异常：NotFoundError（不存在、越权或已删除）、PermissionError（缺少 permission 权限）。
    """
    row = owned_record(ctx, BusinessBot, bot_id, permission)
    if row.status == "deleted":
        raise NotFoundError("Bot 不存在")
    return row


def _binding_counts(row: BusinessBot) -> dict:
    """统计各类绑定数量（只给前端列表页展示，键名沿用 camelCase 以匹配前端字段）。"""
    return {
        "knowledgeBases": len(row.knowledge_base_ids or []),
        "graphs": len(row.graph_ids or []),
        "workflows": len(row.workflow_ids or []),
        "skills": len(row.skill_ids or []),
        "tools": len(row.tool_ids or []),
    }


def bot_view(row: BusinessBot, *, detail: bool = False) -> dict:
    """Bot 的对外序列化；detail=True 时才附带具体绑定 ID 列表。

    列表页只要 bindingCounts（数量），详情页才要具体 id——避免列表接口把全部绑定 id 都吐出来。
    "source" 固定为 "智慧云脑 Cloud Brain"，是前端用来标识数据来源的展示字段。
    """
    result = {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "botType": row.bot_type,
        "modelProfile": row.model_profile,
        "welcomeMessage": row.welcome_message,
        "status": row.status,
        "publishedRevision": row.published_revision,
        "apiEnabled": row.api_enabled,
        "bindingCounts": _binding_counts(row),
        "version": row.version,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
        "source": "智慧云脑 Cloud Brain",
    }
    if detail:
        result.update({
            "instructions": row.instructions,
            "knowledgeBaseIds": row.knowledge_base_ids,
            "graphIds": row.graph_ids,
            "workflowIds": row.workflow_ids,
            "skillIds": row.skill_ids,
            "toolIds": row.tool_ids,
        })
    return result


def revision_view(row: BotRevision) -> dict:
    """Bot 正式修订的对外序列化：snapshot 是发布那一刻冻结的完整配置（Bot 字段 + 绑定关系），

    之后对草稿的任何修改都不会影响它，因此历史调试/API 调用可以精确复现。
    """
    return {
        "id": row.id,
        "botId": row.bot_id,
        "revision": row.revision,
        "reason": row.reason,
        "snapshot": row.snapshot,
        "createdAt": _iso(row.created_at),
    }


def debug_run_view(row: BotDebugRun) -> dict:
    """调试/接口运行的对外序列化；references 是本次运行的全部证据链（含未采信的动作说明）。

    前端据此渲染"回答 + 引用来源"，因此 references 原样下发不做裁剪。
    """
    return {
        "id": row.id,
        "botId": row.bot_id,
        "botRevision": row.bot_revision,
        "channel": row.channel,
        "mode": row.mode,
        "query": row.query,
        "answer": row.answer,
        "references": row.references,
        "status": row.status,
        "errorMessage": row.error_message,
        "durationMs": row.duration_ms,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
    }


def api_key_view(row: BotApiKey) -> dict:
    """API Key 的对外序列化；刻意不返回密钥本体（只在创建时明文返回一次）。

    只给 key_prefix（前缀，供用户辨认是哪把钥匙）与最后使用时间；version 用于撤销时的乐观锁。
    """
    return {
        "id": row.id,
        "botId": row.bot_id,
        "name": row.name,
        "keyPrefix": row.key_prefix,
        "status": row.status,
        "expiresAt": _iso(row.expires_at),
        "lastUsedAt": _iso(row.last_used_at),
        "version": row.version,
        "createdAt": _iso(row.created_at),
    }


def list_bots(ctx: AuthContext, *, keyword: str = "", bot_type: str = "", status: str = "") -> list[dict]:
    """按关键字/类型/状态列出当前账号可见的 Bot（按更新时间倒序，硬上限 500 条）。

    可见性由 visible_records 决定（所有者 + 数据范围 + 授权），软删除的 Bot 一律不返回。
    keyword 同时匹配名称与描述；bot_type 取值受限于 {agent, knowledge, chatflow}，非法值报 422。
    异常：ValidationError —— bot_type 不在允许集合内。
    """
    statement = visible_records(ctx, BusinessBot, "bots.view").where(BusinessBot.status != "deleted")
    if keyword.strip():
        clean = keyword.strip()
        statement = statement.where((BusinessBot.name.contains(clean)) | (BusinessBot.description.contains(clean)))
    if bot_type:
        if bot_type not in {"agent", "knowledge", "chatflow"}:
            raise ValidationError("未知 Bot 类型")
        statement = statement.where(BusinessBot.bot_type == bot_type)
    if status:
        statement = statement.where(BusinessBot.status == status)
    rows = ctx.db.scalars(statement.order_by(BusinessBot.updated_at.desc()).limit(500)).all()
    return [bot_view(row) for row in rows]


def binding_options(ctx: AuthContext) -> dict[str, list[dict]]:
    """Return only resources this actor can currently inspect and bind."""
    # 中文说明：给"绑定资源"下拉框用的候选列表，按权限点逐类决定是否填充——
    # 没有 knowledge.view 就返回空数组而不是报错，这样前端一个接口就能渲染五类选择器。
    # 注意 tools 只列"连接器下的工具"（Tool join Connector），独立工具不在 Bot 可绑定范围内。
    # 权限不足的类型保持空列表，前端据此隐藏对应区域。
    require_permission(ctx, "bots.view")
    permissions = access_policy(ctx).permissions
    result: dict[str, list[dict]] = {
        "knowledgeBases": [], "graphs": [], "workflows": [], "skills": [], "tools": [],
    }
    if "knowledge.view" in permissions:
        rows = ctx.db.scalars(select(KnowledgeBase).where(
            KnowledgeBase.workspace_id == ctx.workspace_id, KnowledgeBase.status == "active",
        ).order_by(KnowledgeBase.name)).all()
        result["knowledgeBases"] = [{"id": row.id, "name": row.name, "status": row.status} for row in rows]
    if "graphs.view" in permissions:
        statement = visible_records(ctx, KnowledgeGraph, "graphs.view").where(KnowledgeGraph.status != "deleted")
        rows = ctx.db.scalars(statement.order_by(KnowledgeGraph.name)).all()
        result["graphs"] = [{"id": row.id, "name": row.name, "status": row.status,
                             "publishedRevision": row.published_revision} for row in rows]
    if "workflows.view" in permissions:
        rows = workflow_service.list_workflows(ctx, limit=500, offset=0)
        result["workflows"] = [{"id": row.id, "name": row.name, "status": row.status,
                                "publishedRevision": row.publishedRevision} for row in rows]
    if "skills.view" in permissions:
        rows = ctx.db.scalars(select(Skill).where(
            Skill.workspace_id == ctx.workspace_id, Skill.status.notin_(("deleted", "archived")),
        ).order_by(Skill.name)).all()
        result["skills"] = [{"id": row.id, "name": row.name, "status": row.status,
                             "published": bool(row.published_version_id)} for row in rows]
    if "connectors.view" in permissions:
        rows = ctx.db.scalars(select(Tool).join(Connector, Tool.connector_id == Connector.id).where(
            Tool.workspace_id == ctx.workspace_id, Tool.status.notin_(("deleted", "archived")),
            Connector.workspace_id == ctx.workspace_id, Connector.status.notin_(("deleted", "archived")),
        ).order_by(Tool.name)).all()
        result["tools"] = [{"id": row.id, "name": row.name, "status": row.status,
                            "connectorId": row.connector_id, "published": bool(row.published_version_id)} for row in rows]
    return result


def _unique_ids(values: list[str], label: str) -> list[str]:
    """绑定 ID 列表去重校验：有重复直接报错（label 用于生成"知识库不能重复绑定"这类文案）。

    不去重而是报错，是因为重复绑定会让运行期重复检索/重复执行同一动作，
    静默去重会掩盖前端的状态错误，用户看不到自己提交了什么。
    异常：ValidationError —— 存在重复项。
    """
    if len(values) != len(set(values)):
        raise ValidationError(f"{label}不能重复绑定")
    return list(values)


def _validate_bindings(ctx: AuthContext, payload: BotInput, *, publishing: bool = False) -> dict:
    """校验并解析全部绑定关系，返回 {knowledgeBases/graphs/workflows/skills/tools: [...]} 快照。

    每个绑定都要过两道关：require_permission（功能权限）+ require_resource（资源级授权），
    因此"能看 Bot 但无该知识库授权"的用户会在保存阶段就被挡住，而不是运行时才失败。
    publishing=True 时额外要求图谱/工作流已发布：正式版本必须绑定不可变快照，
    否则 Bot 会在草稿变更后产生不一致的历史结果；草稿态允许绑定未发布资源（方便先试后发）。
    chatflow 类型要求至少绑定一个工作流（否则该类型没有任何可执行内容）。
    异常：ValidationError（重复绑定、chatflow 无工作流、发布态资源未发布）、
        NotFoundError（资源不存在/不可用）、权限异常。
    副作用：只读校验，不写库；返回的 resources 会被 publish 流程冻结进 revision.snapshot。
    """
    knowledge_base_ids = _unique_ids(payload.knowledge_base_ids, "知识库")
    graph_ids = _unique_ids(payload.graph_ids, "图谱")
    workflow_ids = _unique_ids(payload.workflow_ids, "工作流")
    skill_ids = _unique_ids(payload.skill_ids, "Skill")
    tool_ids = _unique_ids(payload.tool_ids, "工具")
    if payload.bot_type == "chatflow" and not workflow_ids:
        raise ValidationError("Chatflow 类型至少需要绑定一个工作流")

    resources: dict[str, list[dict]] = {"knowledgeBases": [], "graphs": [], "workflows": [], "skills": [], "tools": []}
    if knowledge_base_ids:
        require_permission(ctx, "knowledge.view")
        rows = ctx.db.scalars(select(KnowledgeBase).where(
            KnowledgeBase.workspace_id == ctx.workspace_id,
            KnowledgeBase.id.in_(knowledge_base_ids),
            KnowledgeBase.status == "active",
        )).all()
        if {row.id for row in rows} != set(knowledge_base_ids):
            raise NotFoundError("一个或多个知识库不存在或不可用")
        for row in rows:
            require_resource(ctx, "knowledge.view", row)
            resources["knowledgeBases"].append({"id": row.id, "name": row.name, "updatedAt": _iso(row.updated_at)})

    for graph_id in graph_ids:
        row = graph_service.get_graph(ctx, graph_id, "graphs.view")
        if publishing and (row.status != "published" or row.published_revision is None):
            raise ValidationError(f"图谱“{row.name}”尚未发布，不能进入 Bot 正式版本")
        resources["graphs"].append({
            "id": row.id, "name": row.name,
            "revision": row.published_revision if publishing else None,
            "ontologyRevision": row.ontology_revision,
        })

    for workflow_id in workflow_ids:
        row = workflow_service.get_workflow(ctx, workflow_id, "workflows.view")
        if publishing and (row.status != "published" or row.published_revision is None):
            raise ValidationError(f"工作流“{row.name}”尚未发布，不能进入 Bot 正式版本")
        resources["workflows"].append({
            "id": row.id, "name": row.name,
            "revision": row.published_revision if publishing else None,
        })

    if skill_ids:
        require_permission(ctx, "skills.view")
        rows = ctx.db.scalars(select(Skill).where(
            Skill.workspace_id == ctx.workspace_id,
            Skill.id.in_(skill_ids),
            Skill.status.notin_(("deleted", "archived")),
        )).all()
        if {row.id for row in rows} != set(skill_ids):
            raise NotFoundError("一个或多个 Skill 不存在或已归档，请重新选择")
        for row in rows:
            require_resource(ctx, "skills.view", row)
            if publishing and not row.published_version_id:
                raise ValidationError(f"Skill“{row.name}”尚未发布，不能进入 Bot 正式版本")
            resources["skills"].append({"id": row.id, "name": row.name, "versionId": row.published_version_id})

    if tool_ids:
        require_permission(ctx, "connectors.view")
        rows = ctx.db.scalars(select(Tool).where(
            Tool.workspace_id == ctx.workspace_id,
            Tool.id.in_(tool_ids),
            Tool.status.notin_(("deleted", "archived")),
        )).all()
        if {row.id for row in rows} != set(tool_ids):
            raise NotFoundError("一个或多个工具不存在或已归档，请重新选择")
        connectors = {row.id: row for row in ctx.db.scalars(select(Connector).where(
            Connector.workspace_id == ctx.workspace_id,
            Connector.id.in_({item.connector_id for item in rows}),
            Connector.status.notin_(("deleted", "archived")),
        )).all()}
        for row in rows:
            connector = connectors.get(row.connector_id)
            if not connector:
                raise NotFoundError("工具所属连接器不存在或已归档，请重新选择")
            require_resource(ctx, "connectors.view", connector)
            if publishing and not row.published_version_id:
                raise ValidationError(f"工具“{row.name}”尚未发布，不能进入 Bot 正式版本")
            resources["tools"].append({
                "id": row.id, "name": row.name, "versionId": row.published_version_id,
                "connectorId": row.connector_id,
            })
    return resources


def _payload_from_bot(row: BusinessBot) -> BotInput:
    """把已存的 Bot 行反序列化成 BotInput，供"重新校验当前绑定"复用同一套校验逻辑。

    用于 debug_bot（跑草稿前校验）与 publish_bot（发布前以 publishing=True 校验），
    这样"保存时能过、发布时也能过"的规则只有一份实现。
    """
    return BotInput(
        name=row.name,
        description=row.description,
        botType=row.bot_type,
        instructions=row.instructions,
        modelProfile=row.model_profile,
        knowledgeBaseIds=row.knowledge_base_ids,
        graphIds=row.graph_ids,
        workflowIds=row.workflow_ids,
        skillIds=row.skill_ids,
        toolIds=row.tool_ids,
        welcomeMessage=row.welcome_message,
        apiEnabled=row.api_enabled,
        version=row.version,
    )


def save_bot(ctx: AuthContext, payload: BotInput, bot_id: str | None = None) -> dict:
    """新建或更新 Bot 草稿配置，返回详情视图。

    参数：bot_id 为 None 表示新建（要求 bots.create 且不允许带 version）；
        传入则更新（要求 bots.edit 且必须带正确的 version，乐观锁）。
    存档状态回退：archived/deleted 的 Bot 被再次编辑时自动回到 "draft"，
        这样"归档后继续改"不会留下一个状态自相矛盾的记录。
    校验顺序：权限 → 绑定校验（含逐资源授权）→ 版本校验。
    异常：ValidationError（绑定/版本非法）、NotFoundError（Bot 或绑定资源不存在）、
        ConflictError（名称重复或并发修改）。
    副作用：写 business_bots 并写审计（action=bots.create / bots.update，metadata 记类型与绑定数量）。
    """
    permission = "bots.edit" if bot_id else "bots.create"
    require_permission(ctx, permission)
    _validate_bindings(ctx, payload)
    if bot_id:
        row = get_bot(ctx, bot_id, permission)
        _version(row, payload.version)
    else:
        _version(None, payload.version, creating=True)
        row = BusinessBot(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    row.name = payload.name.strip()
    row.description = payload.description.strip()
    row.bot_type = payload.bot_type
    row.instructions = payload.instructions.strip()
    row.model_profile = payload.model_profile.strip()
    row.knowledge_base_ids = list(payload.knowledge_base_ids)
    row.graph_ids = list(payload.graph_ids)
    row.workflow_ids = list(payload.workflow_ids)
    row.skill_ids = list(payload.skill_ids)
    row.tool_ids = list(payload.tool_ids)
    row.welcome_message = payload.welcome_message.strip()
    row.api_enabled = payload.api_enabled
    if row.status in {"archived", "deleted"}:
        row.status = "draft"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="bots.update" if bot_id else "bots.create", resource_type="business_bot", resource_id=row.id,
          metadata={"type": row.bot_type, "bindings": _binding_counts(row)})
    _commit(ctx, "Bot 名称已存在")
    return bot_view(row, detail=True)


def remove_bot(ctx: AuthContext, bot_id: str, version: int) -> None:
    """软删除 Bot，并连带撤销其全部 active 状态的 API Key。

    连带撤权是必须的：Bot 被删除后若 Key 仍然有效，外部系统会继续调用到一个已下线的能力。
    只改状态（Bot 置 deleted、Key 置 revoked），不删数据，保留审计与历史运行记录。
    异常：NotFoundError（Bot 不存在）、ConflictError（版本冲突、删除提交失败）。
    副作用：写 business_bots 与 bot_api_keys，写审计（metadata 记撤销了几把 Key）。
    """
    row = get_bot(ctx, bot_id, "bots.delete")
    _version(row, version)
    row.status = "deleted"
    keys = ctx.db.scalars(select(BotApiKey).where(
        BotApiKey.workspace_id == ctx.workspace_id, BotApiKey.bot_id == row.id, BotApiKey.status == "active",
    )).all()
    for key in keys:
        key.status = "revoked"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="bots.delete", resource_type="business_bot", resource_id=row.id,
          metadata={"revoked_keys": len(keys)})
    _commit(ctx, "Bot 删除失败")


def _draft_snapshot(row: BusinessBot, resources: dict) -> dict:
    """冻结发布快照：Bot 配置 + 已解析的绑定资源（含图谱/工作流的 revision）。

    只挑发布时真正需要的字段（不含 version/status 等会变化的治理字段），
    这样历史修订的语义就是"当时可用的能力集合"，不会因为后续改名/改版而漂移。
    """
    return {
        "bot": {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "botType": row.bot_type,
            "instructions": row.instructions,
            "modelProfile": row.model_profile,
            "welcomeMessage": row.welcome_message,
            "apiEnabled": row.api_enabled,
        },
        "bindings": resources,
        "createdAt": datetime.utcnow().isoformat(),
    }


def publish_bot(ctx: AuthContext, bot_id: str, payload: PublishInput) -> dict:
    """发布 Bot 的正式版本：冻结快照、写入 revision 并返回详情视图。

    版本号取自现有最大 revision + 1（不复用、不回收），因此即使删除中间修订，
    历史调试记录里的 bot_revision 也不会指向另一个语义的快照。
    发布前会以 publishing=True 重新校验绑定，强制要求图谱/工作流已发布（见 _validate_bindings）。
    异常：NotFoundError（Bot 不存在）、ConflictError（版本冲突或 revision 竞争）。
    副作用：写 bot_revisions、更新 business_bots（status=published、published_revision）、
        写审计 action=bots.publish。
    """
    row = get_bot(ctx, bot_id, "bots.publish")
    _version(row, payload.version)
    resources = _validate_bindings(ctx, _payload_from_bot(row), publishing=True)
    revision = (ctx.db.scalar(select(func.max(BotRevision.revision)).where(BotRevision.bot_id == row.id)) or 0) + 1
    ctx.db.add(BotRevision(
        workspace_id=ctx.workspace_id,
        owner_user_id=ctx.user_id,
        bot_id=row.id,
        revision=revision,
        snapshot=_draft_snapshot(row, resources),
        reason=payload.reason.strip(),
    ))
    row.status = "published"
    row.published_revision = revision
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="bots.publish", resource_type="business_bot", resource_id=row.id,
          metadata={"revision": revision, "api_enabled": row.api_enabled})
    _commit(ctx, "Bot 发布修订冲突，请刷新后重试")
    return bot_view(row, detail=True)


def list_revisions(ctx: AuthContext, bot_id: str) -> list[dict]:
    """列出该 Bot 的历史正式修订，按 revision 倒序，最多 100 条。异常：NotFoundError。"""
    bot = get_bot(ctx, bot_id, "bots.view")
    rows = ctx.db.scalars(select(BotRevision).where(
        BotRevision.workspace_id == ctx.workspace_id,
        BotRevision.bot_id == bot.id,
    ).order_by(BotRevision.revision.desc()).limit(100)).all()
    return [revision_view(row) for row in rows]


def _revision(ctx: AuthContext, bot: BusinessBot, revision: int | None = None) -> BotRevision:
    """取正式修订行；revision 为 None 时用 bot.published_revision（即"当前正式版"）。

    异常：ValidationError（Bot 从未发布）、NotFoundError（指定修订号不存在，通常是历史数据被清理）。
    """
    selected = revision or bot.published_revision
    if selected is None:
        raise ValidationError("Bot 尚未发布正式版本")
    row = ctx.db.scalar(select(BotRevision).where(
        BotRevision.workspace_id == ctx.workspace_id,
        BotRevision.bot_id == bot.id,
        BotRevision.revision == selected,
    ))
    if not row:
        raise NotFoundError("Bot 正式修订不存在")
    return row


def _tokens(query: str) -> list[str]:
    """把问题串切成打分用词元：英文/数字取 ≥2 字符的整词，中文取所有相邻 2 字组合。

    中文按 2-gram 切是为了不依赖分词器也能工作（与 knowledge/service._keyword_units 同思路，
    但这里只切 2 字、且不做停用词过滤）。用 casefold 而非 lower 以兼容部分非 ASCII 大小写。
    最后按出现顺序去重并截断到 50 个，避免超长提问把打分循环拖长。
    """
    lowered = query.casefold()
    words = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", lowered)
    result: list[str] = []
    for word in words:
        if re.fullmatch(r"[\u4e00-\u9fff]+", word) and len(word) > 2:
            result.extend(word[index:index + 2] for index in range(len(word) - 1))
        else:
            result.append(word)
    return list(dict.fromkeys(result))[:50]


def _score(query: str, content: str, tokens: list[str]) -> int:
    """Bot 自写的整数打分：整串包含 +20，再对每个命中词元加 1 + min(5, 出现次数)。

    与知识库检索的差异（这是刻意的，不是遗漏）：
        - 是纯字符统计的启发式分数，没有 TF-IDF/BM25 的文档频率归一化，也不做向量相似度，
          因此长文档天然更容易命中更多词元、拿到更高分；
        - 词元出现次数被截断在 5 次，避免关键词刷分；
        - 返回 0 表示完全不相关，调用方据此丢弃该候选（等价于一个隐含的 min_score 阈值 0）。
    该函数只用于 Bot 运行时的证据筛选，不参与知识库检索接口的排序。
    """
    folded = content.casefold()
    exact = 20 if query.casefold() in folded else 0
    return exact + sum(1 + min(5, folded.count(token)) for token in tokens if token in folded)


def _knowledge_references(
    db: Session, workspace_id: str, knowledge_base_ids: list[str], query: str,
    *, auth_ctx: AuthContext | None = None,
) -> list[dict]:
    """Bot 运行时的知识证据检索：直接在 SQL 上自写打分，返回最多 8 条证据。

    与知识库检索接口（knowledge/service.search）的关键差异——Bot 走的是一条独立、更简单的路径：
        - 完全不读知识库设置：忽略 retrieval_mode / top_k / min_score / rerank_enabled，
          即使用户在知识库设置里选了 semantic + 开启重排，Bot 这里也只用关键词启发式；
        - 条数与阈值固定写死：命中数上限 8 条（ranked[:8]），阈值等价于 min_score=0（score>0 即保留），
          证据正文截断到 1200 字；
        - 不经过 Elasticsearch、不算查询向量，因此不受 ES 可用性影响，也不会因向量模型不可用而失败；
        - 候选集上限 5000 条（order_by document_id, ordinal + limit），大库只覆盖前 5000 个分块。
    过滤条件与知识库检索的 SQL 复核保持一致（文档 ready/processing、知识库 active、文件 ready 且
    未删除、分段未被停用），保证"Bot 看不到的内容"与"检索接口看不到的内容"口径相同。
    排序：分数倒序，同分时按 (document_id, ordinal) 稳定排序，保证同样的问题得到同样的证据顺序。
    权限：传了 auth_ctx 时先重绑 knowledge.view 数据范围并逐个知识库做资源级校验
    （同一会话上前一阶段可能装配了别的加载器条件，所以必须重新绑定）；
    知识库被停用或无权访问时抛 NotFoundError，而不是静默返回空。
    返回：type="knowledge_chunk" 的证据项，带 knowledgeBaseId/documentId/chunkId/ordinal/
    location/score/content——注意这里用的是 camelCase 字段名（Bot 证据链对外格式），
    与知识库检索接口的 snake_case 字段（file_id/location 等）不是同一套契约。
    """
    if not knowledge_base_ids:
        return []
    from app.modules.knowledge.models import KnowledgeChunkMetadata

    if auth_ctx is not None:
        # The same SQLAlchemy session is reused by the Bot run.  A later graph
        # or connector phase may have installed a different loader criterion;
        # restore the knowledge scope before loading KBs and their source files.
        bind_data_scope(auth_ctx, "knowledge.view")
        require_permission(auth_ctx, "knowledge.view")
        visible_ids = set(db.scalars(select(KnowledgeBase.id).where(
            KnowledgeBase.workspace_id == workspace_id,
            KnowledgeBase.id.in_(knowledge_base_ids),
            KnowledgeBase.status == "active",
        )).all())
        if visible_ids != set(knowledge_base_ids):
            raise NotFoundError("Bot 绑定的知识库已停用或当前账号无权访问")
        for kb_id in knowledge_base_ids:
            require_resource(auth_ctx, "knowledge.view", db.get(KnowledgeBase, kb_id))

    tokens = _tokens(query)
    chunks = db.scalars(
        select(KnowledgeChunk)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .join(KnowledgeBase, KnowledgeBase.id == KnowledgeChunk.knowledge_base_id)
        .join(File, File.id == KnowledgeDocument.file_id)
        .outerjoin(KnowledgeChunkMetadata, KnowledgeChunkMetadata.chunk_id == KnowledgeChunk.id)
        .where(
            KnowledgeChunk.workspace_id == workspace_id,
            KnowledgeChunk.knowledge_base_id.in_(knowledge_base_ids),
            KnowledgeDocument.workspace_id == workspace_id,
            KnowledgeDocument.status.in_(("ready", "processing")),
            KnowledgeBase.workspace_id == workspace_id,
            KnowledgeBase.status == "active",
            File.workspace_id == workspace_id,
            File.status == "ready",
            File.deleted_at.is_(None),
            (KnowledgeChunkMetadata.id.is_(None) | KnowledgeChunkMetadata.enabled.is_(True)),
        )
        .order_by(KnowledgeChunk.document_id, KnowledgeChunk.ordinal)
        .limit(5000)
    ).all()
    ranked = []
    for chunk in chunks:
        score = _score(query, chunk.text, tokens)
        if score:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1].document_id, item[1].ordinal))
    return [{
        "type": "knowledge_chunk",
        "knowledgeBaseId": row.knowledge_base_id,
        "documentId": row.document_id,
        "chunkId": row.id,
        "ordinal": row.ordinal,
        "location": row.location or {},
        "score": score,
        "content": row.text[:1200],
    } for score, row in ranked[:8]]


def _graph_snapshot(db: Session, workspace_id: str, graph_id: str, revision: int | None) -> tuple[list[dict], list[dict]]:
    """取图谱的 (实体列表, 关系列表)：revision 有值读不可变快照，为 None 读当前草稿。

    快照路径：从 graph_revisions 读冻结内容，缺失即报错而**不回退到草稿**——
    绑定固定修订的 Bot 必须可复现，悄悄用草稿会产出与历史不一致的答案。
    草稿路径：直接查实体/关系表并排除 status=="deleted" 的行。
    异常：ValidationError —— 指定的正式修订不存在。
    """
    if revision is not None:
        row = db.scalar(select(GraphRevision).where(
            GraphRevision.workspace_id == workspace_id,
            GraphRevision.graph_id == graph_id,
            GraphRevision.revision == revision,
        ))
        if row is None:
            raise ValidationError("Bot 绑定的图谱正式修订不存在，不能回退到当前草稿")
        return list(row.snapshot.get("entities", [])), list(row.snapshot.get("relations", []))
    entities = db.scalars(select(GraphEntity).where(
        GraphEntity.workspace_id == workspace_id,
        GraphEntity.graph_id == graph_id,
        GraphEntity.status != "deleted",
    )).all()
    relations = db.scalars(select(GraphRelation).where(
        GraphRelation.workspace_id == workspace_id,
        GraphRelation.graph_id == graph_id,
        GraphRelation.status != "deleted",
    )).all()
    return [graph_service.entity_view(row) for row in entities], [graph_service.relation_view(row) for row in relations]


def _graph_references(
    db: Session, workspace_id: str, graph_bindings: list[dict], query: str,
    *, auth_ctx: AuthContext | None = None,
) -> list[dict]:
    """Bot 运行时的图谱证据检索：对实体名/业务键/属性做与知识分块同一套自写打分，最多返回 8 条。

    打分要点：把实体名、businessKey 与 properties（JSON 序列化）拼成一段文本再 _score，
    因此属性值也能被命中。命中后附带该实体的邻居关系（每个实体最多 20 条邻居），
    让模型能看到"实体之间的关系"而不只是孤立的实体。
    与知识分块同样固定上限 8 条、阈值 0，排序为分数倒序 + (graphId, entityId) 稳定排序。
    权限与状态门禁：传了 auth_ctx 时重绑 graphs.view 并逐个 get_graph 校验；
    当绑定带固定 revision 且图谱已非 published（被归档/删除）时抛 ValidationError——
    这是显式撤销点；而"绑定快照 + 后续再次发布"仍然合法，因为快照本身不可变。
    异常：ValidationError（修订号非法、或绑定修订的图谱已停用）。
    """
    if auth_ctx is not None:
        # Restore the graph-specific scope when this phase follows knowledge,
        # workflow or connector resolution on the same session.
        bind_data_scope(auth_ctx, "graphs.view")
        require_permission(auth_ctx, "graphs.view")
        for binding in graph_bindings:
            graph = graph_service.get_graph(
                auth_ctx, str(binding.get("id") or ""), "graphs.view",
            )
            revision = binding.get("revision")
            if revision is not None:
                try:
                    revision = int(revision)
                except (TypeError, ValueError) as exc:
                    raise ValidationError("Bot 绑定的图谱正式修订无效") from exc
            if revision is not None and graph.status != "published":
                # The binding carries an immutable historical snapshot.  A
                # later publication leaves it valid; archiving/deleting the
                # graph is the explicit revocation gate.
                raise ValidationError("Bot 绑定的图谱已停用，不能执行固定修订")
    tokens = _tokens(query)
    ranked: list[tuple[int, dict]] = []
    for binding in graph_bindings:
        revision = binding.get("revision")
        if revision is not None:
            try:
                revision = int(revision)
            except (TypeError, ValueError) as exc:
                raise ValidationError("Bot 绑定的图谱正式修订无效") from exc
        entities, relations = _graph_snapshot(db, workspace_id, binding["id"], revision)
        entity_by_id = {item["id"]: item for item in entities}
        for entity in entities:
            content = " ".join((str(entity.get("name", "")), str(entity.get("businessKey", "")),
                                json.dumps(entity.get("properties", {}), ensure_ascii=False)))
            score = _score(query, content, tokens)
            if not score:
                continue
            neighbors = []
            for relation in relations:
                if relation.get("sourceEntityId") == entity["id"] or relation.get("targetEntityId") == entity["id"]:
                    other_id = (relation.get("targetEntityId") if relation.get("sourceEntityId") == entity["id"]
                                else relation.get("sourceEntityId"))
                    neighbors.append({
                        "relation": relation.get("relationName") or relation.get("relationTypeId"),
                        "entityId": other_id,
                        "entityName": (entity_by_id.get(other_id) or {}).get("name"),
                    })
            ranked.append((score, {
                "type": "graph_entity",
                "graphId": binding["id"],
                "graphRevision": revision,
                "entityId": entity["id"],
                "name": entity.get("name"),
                "businessKey": entity.get("businessKey"),
                "properties": entity.get("properties", {}),
                "neighbors": neighbors[:20],
                "score": score,
            }))
    ranked.sort(key=lambda item: (-item[0], item[1]["graphId"], item[1]["entityId"]))
    return [item for _, item in ranked[:8]]


def _configuration(row: BusinessBot, revision: BotRevision | None) -> tuple[dict, dict]:
    """取 (配置, 绑定关系)：revision 有值用冻结快照，为 None 用当前草稿。

    两种来源刻意返回同形状的 dict，让 _run_bot 之后的逻辑（检索、动作选择、回答）完全不必知道
    自己跑的是正式版还是草稿。草稿路径会把 graph_ids 补成 {"id":..., "revision": None}
    以对齐快照里的绑定结构（revision=None 即"跟随最新草稿"）。
    """
    if revision:
        return revision.snapshot["bot"], revision.snapshot.get("bindings", {})
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "botType": row.bot_type,
        "instructions": row.instructions,
        "modelProfile": row.model_profile,
        "welcomeMessage": row.welcome_message,
        "apiEnabled": row.api_enabled,
    }, {
        "knowledgeBases": [{"id": item} for item in row.knowledge_base_ids],
        "graphs": [{"id": item, "revision": None} for item in row.graph_ids],
        "workflows": [{"id": item, "revision": None} for item in row.workflow_ids],
        "skills": [{"id": item} for item in row.skill_ids],
        "tools": [{"id": item} for item in row.tool_ids],
    }


def _grounded_references(
    db: Session, workspace_id: str, bindings: dict, query: str,
    *, auth_ctx: AuthContext | None = None, graph_auth_ctx: AuthContext | None = None,
) -> list[dict]:
    """组装全部"有依据"的证据：知识分块证据 + 图谱实体证据（各自独立打分，直接拼接）。

    两路证据不做合并排序或分数归一：它们的分数来自同一套 _score，但语义不同（文本命中 vs 实体命中），
    调用方（_retrieval_answer / _model_answer）按顺序编号展示，编号即模型引用序号。
    参数 auth_ctx / graph_auth_ctx 分开传入是刻意的：两个阶段需要各自的数据范围绑定与权限点，
    共用一个上下文会让"有图谱权限但无知识库权限"的用户拿到不该看的证据。
    """
    knowledge_ids = [item["id"] for item in bindings.get("knowledgeBases", [])]
    return [
        *_knowledge_references(db, workspace_id, knowledge_ids, query, auth_ctx=auth_ctx),
        *_graph_references(
            db, workspace_id, bindings.get("graphs", []), query, auth_ctx=graph_auth_ctx,
        ),
    ]


def _bot_context(db: Session, workspace_id: str, user_id: str, permission: str, *, run_id: str,
                 auth_context: AuthContext | None = None) -> AuthContext:
    """重查 Bot 执行身份；服务调用保留初始范围并核验原令牌的当前状态。"""
    from app.common.deps import _workspace_is_active, resolve_service_principal
    from app.modules.business_ai.context import auth_context_from_tool

    execution = build_context(
        db=db, workspace_id=workspace_id, user_id=user_id, run_id=run_id,
        conversation_id="", allow_network=False,
    )
    if auth_context is not None:
        if (auth_context.db is not db or auth_context.workspace_id != workspace_id
                or auth_context.user_id != user_id):
            raise AuthError("Bot 执行身份与原请求不一致")
        supplied = auth_context
        if auth_context.client_id or auth_context.client_scopes is not None:
            from app.modules.system_tools.models import IntegrationAccessToken

            if not auth_context.client_id or auth_context.client_scopes is None or not auth_context.token:
                raise AuthError("Bot 服务调用缺少可验证的原令牌")
            service = db.scalar(select(IntegrationAccessToken).where(
                IntegrationAccessToken.token_digest == hashlib.sha256(auth_context.token.encode("utf-8")).hexdigest(),
            ).execution_options(populate_existing=True))
            principal = resolve_service_principal(db, service)
            if (principal.workspace_id != workspace_id or principal.user.id != user_id
                    or principal.client_id != auth_context.client_id):
                raise AuthError("Bot 服务令牌归属已变化")
            # 当前客户端与令牌只能收窄原请求，不能在长调用中追加权限或恢复管理员身份。
            supplied = AuthContext(db=db, user=principal.user, workspace=auth_context.workspace,
                role=auth_context.role, token="", client_id=auth_context.client_id,
                client_scopes=auth_context.client_scopes.intersection(principal.client_scopes))
        execution["auth_context"] = supplied
    ctx = auth_context_from_tool(execution)
    if not _workspace_is_active(db, ctx.workspace):
        raise AuthError("Bot 执行工作区已停用")
    require_permission(ctx, "bots.execute")
    require_permission(ctx, permission)
    ctx.request_permission = permission
    bind_data_scope(ctx, permission)
    return ctx


def _workflow_inputs(snapshot: dict, query: str) -> dict:
    """Map a Bot message to a published workflow's declared start input."""
    graph = snapshot.get("graph") if isinstance(snapshot, dict) else None
    start = next((node for node in (graph or {}).get("nodes", [])
                  if node.get("kind") == "start"), None)
    variables = ((start or {}).get("config") or {}).get("variables") or []
    if not variables:
        return {"input": query}
    declared = [item for item in variables if isinstance(item, dict) and item.get("variable")]
    if not declared:
        return {"input": query}
    preferred = {"input", "query", "message", "text"}
    matches = [item for item in declared if item.get("variable") in preferred]
    if len(matches) == 1:
        return {matches[0]["variable"]: query}
    required = [item for item in declared if item.get("required")]
    if len(required) == 1:
        return {required[0]["variable"]: query}
    if len(declared) == 1:
        return {declared[0]["variable"]: query}
    raise ValidationError("Bot 消息无法唯一映射到工作流的多个必填开始参数")


def _workflow_action_name(workflow_id: str) -> str:
    """把工作流 ID 映射成稳定的动作名（wf_<id>），用于在动作选择阶段标识该工作流。"""
    return f"workflow__{workflow_id}"


def _resolve_bound_workflow(
    db: Session, *, bot: BusinessBot, binding: dict,
    actor_user_id: str, runtime_run_id: str, auth_context: AuthContext | None = None,
):
    """保留原调用身份，按当前执行权限解析固定或草稿工作流版本。"""
    # 从绑定列表里解析出可执行的工作流（要求已发布且有正式修订），返回 (row, revision_id)。
    #
    # 返回 None 表示该绑定当前不可执行（未发布/已归档/无修订），调用方据此给出 action_guidance
    # 提示而不是直接报错，让"绑定里有一个不可用工作流"不至于让整个 Bot 运行失败。
    #
    workflow_id = str(binding.get("id") or "")
    pinned_revision = binding.get("revision")
    if not workflow_id:
        raise ValidationError("Bot 工作流绑定缺少标识")
    ctx = _bot_context(
        db, bot.workspace_id, actor_user_id, "workflows.execute", run_id=runtime_run_id,
        auth_context=auth_context,
    )
    workflow = workflow_service.get_workflow(ctx, workflow_id, "workflows.execute")
    if pinned_revision is None:
        if workflow.status in {"deleted", "disabled"}:
            raise ValidationError(f"工作流“{workflow.name}”已停用，不能执行")
        run_mode = "draft"
        release = workflow_service.revision(ctx, workflow, workflow.version)
    else:
        try:
            expected_revision = int(pinned_revision)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Bot 工作流绑定的正式修订无效") from exc
        # A Bot binding pins an immutable historical revision.  Publishing a
        # newer draft must not silently change that Bot's behavior; the
        # workflow's explicit disabled/deleted state is the revocation gate.
        if workflow.status != "published":
            raise ValidationError(f"工作流“{workflow.name}”已停用，不能执行固定修订")
        run_mode = "published"
        release = workflow_service.revision(ctx, workflow, expected_revision)
    return ctx, workflow, release, run_mode


def _workflow_action_spec(
    db: Session, *, bot: BusinessBot, binding: dict,
    actor_user_id: str, runtime_run_id: str, auth_context: AuthContext | None = None,
) -> dict:
    """向动作选择器仅提供已授权工作流声明的开始字段。"""
    # 把绑定的工作流转成供模型选择的动作描述（action spec）。
    #
    # 只暴露动作名、说明与参数 schema，不暴露工作流内部结构；模型据此决定要不要调用它。
    #
    _ctx, workflow, release, _run_mode = _resolve_bound_workflow(
        db, bot=bot, binding=binding,
        actor_user_id=actor_user_id, runtime_run_id=runtime_run_id,
        auth_context=auth_context,
    )
    graph = release.snapshot.get("graph") if isinstance(release.snapshot, dict) else {}
    start = next((node for node in (graph or {}).get("nodes", [])
                  if node.get("kind") == "start"), None)
    variables = ((start or {}).get("config") or {}).get("variables") or []
    properties: dict[str, dict] = {}
    required: list[str] = []
    type_map = {
        "number": "number", "checkbox": "boolean", "json": "object",
        "json_object": "object", "file-list": "array",
    }
    for variable in variables:
        if not isinstance(variable, dict) or not variable.get("variable"):
            continue
        name = str(variable["variable"])
        field = {
            "type": type_map.get(variable.get("type"), "string"),
            "description": str(
                variable.get("hint") or variable.get("label") or name
            )[:500],
        }
        if variable.get("type") == "select" and variable.get("options"):
            field["enum"] = [str(item) for item in variable["options"][:100]]
        properties[name] = field
        if variable.get("required"):
            required.append(name)
    if not properties:
        properties = {
            "input": {
                "type": "string",
                "description": "用户明确要求执行此工作流时传入的任务内容",
            },
        }
        required = ["input"]
    description = str(workflow.description or workflow.name)[:1000]
    if any(node.get("kind") == "output-table" for node in (graph or {}).get("nodes", [])):
        description += "；该流程包含实际写入，仍需工作流入口的 confirmWrites 明确确认"
    return {
        "kind": "workflow",
        "name": _workflow_action_name(workflow.id),
        "display_name": workflow.name,
        "description": description,
        "binding": binding,
        "schema": {
            "type": "object", "properties": properties,
            "required": required, "additionalProperties": False,
        },
    }


def _bound_action_catalog(
    db: Session, *, bot: BusinessBot, bindings: dict,
    actor_user_id: str, runtime_run_id: str, auth_context: AuthContext | None = None,
) -> tuple[list[dict], list[str]]:
    """按原调用身份构造可执行的绑定动作目录，此处不执行动作。"""
    # 汇总当前 Bot 可选的动作用户目录（工作流 + 工具），供动作选择阶段一次性提供给模型。
    catalog: list[dict] = []
    unavailable: list[str] = []
    for binding in bindings.get("workflows", []):
        try:
            catalog.append(_workflow_action_spec(
                db, bot=bot, binding=binding,
                actor_user_id=actor_user_id, runtime_run_id=runtime_run_id,
                auth_context=auth_context,
            ))
        except Exception as exc:
            unavailable.append(f"workflow:{binding.get('id') or '?'}:{str(exc)[:180]}")
    for binding in bindings.get("tools", []):
        try:
            _ctx, tool, tool_version, connector_version, spec = _bound_tool_spec(
                db, bot.workspace_id, binding, actor_user_id, runtime_run_id,
                auth_context=auth_context,
            )
            catalog.append({
                "kind": "tool", "name": spec.name, "display_name": spec.name,
                "binding": binding, "spec": spec,
                "tool_id": tool.id, "tool_version_id": tool_version.id,
                "connector_version_id": connector_version.id,
            })
        except Exception as exc:
            unavailable.append(f"tool:{binding.get('id') or '?'}:{str(exc)[:180]}")
    return catalog, unavailable


def _select_bound_actions(
    db: Session, *, bot: BusinessBot, bindings: dict, query: str,
    configuration: dict, actor_user_id: str, runtime_run_id: str,
    feedback: list[str] | None = None,
    auth_context: AuthContext | None = None,
) -> list[dict]:
    """让模型至多选择一个已授权的绑定动作；没有函数调用就不执行动作。"""
    # 让模型在"可用动作目录"里选出本次要执行的动作，返回被选中的动作名集合。
    #
    # 失败即降级：模型不可用或返回无法解析时返回空集合（本次不执行任何动作），
    # 并在 feedback 里追加说明文案，由调用方转成 action_guidance 证据提示用户"本次未执行动作"。
    # 因此"动作选择失败"不会中断 Bot 运行，只是退化为纯检索回答。
    # 副作用：调用模型；可能写模型用量记录。
    #
    catalog, unavailable = _bound_action_catalog(
        db, bot=bot, bindings=bindings,
        actor_user_id=actor_user_id, runtime_run_id=runtime_run_id,
        auth_context=auth_context,
    )
    if not catalog:
        if unavailable and feedback is not None:
            feedback.append("绑定的能力当前不可用：" + "、".join(unavailable[:10]) + "。本次未执行这些动作。")
        return []
    tools = []
    by_name: dict[str, dict] = {}
    for action in catalog:
        if action["kind"] == "workflow":
            function = {
                "type": "function",
                "function": {
                    "name": action["name"],
                    "description": action["display_name"] + "：" + action["description"],
                    "parameters": action["schema"],
                },
            }
        else:
            function = action["spec"].to_openai_function()
        tools.append(function)
        by_name[action["name"]] = action
    unavailable_hint = (
        "以下绑定当前不可用，不能调用：" + "、".join(unavailable[:10])
        if unavailable else ""
    )
    response = model.chat_for_run(
        configuration.get("modelProfile") or "chat",
        [
            {
                "role": "system",
                "content": (
                    "你是企业 Bot 的动作选择器。只有当用户明确要求执行某个已绑定动作，"
                    "并且请求中已有足够参数时，才生成函数调用；普通知识问答、闲聊、"
                    "询问能力列表不得调用任何函数。每次最多选择一个动作，不要为了测试而调用。"
                    "不得猜测资源 ID、金额、对象或其他必填参数；缺少参数时返回普通文本让用户补充。"
                    "涉及写入、删除、发送、提交、更新或其他副作用的动作，必须以用户明确的执行意图为前提，"
                    "不能从普通询问推断执行。\n" + unavailable_hint
                ),
            },
            {"role": "user", "content": query},
        ],
        tools=tools,
        temperature=0.0,
        workspace_id=bot.workspace_id,
        usage_context={"source": "bot_action_selector", "source_id": runtime_run_id},
    )
    selected: list[dict] = []
    for call in (response.get("tool_calls") or [])[:1]:
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        action = by_name.get(name)
        if action is None:
            raise ValidationError("模型选择了未绑定的 Bot 动作")
        raw = function.get("arguments")
        try:
            arguments = json.loads(raw or "{}") if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise ValidationError("模型返回的 Bot 动作参数不是合法 JSON") from exc
        if not isinstance(arguments, dict):
            raise ValidationError("Bot 动作参数必须是 JSON 对象")
        selected.append({
            "name": name, "kind": action["kind"], "binding": action["binding"],
            "arguments": arguments, "model_call_id": call.get("id"),
        })
    if not selected and feedback is not None and str(response.get("content") or "").strip():
        feedback.append(str(response["content"]).strip()[:4000])
    return selected


def _bounded_action_value(value, *, limit: int = 12000):
    """Keep action evidence useful without copying unbounded node payloads."""
    # 把动作执行结果压缩成可安全放进证据链的形态：超长文本按 limit 截断，其余原样返回。
    #
    # 目的是防止一个巨大的工具返回值把 BotDebugRun.references 撑爆、并挤占模型上下文。
    #
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)[:limit]
    if len(encoded) <= limit:
        return value
    return {"truncated": True, "preview": encoded[:limit]}


def _execute_bound_workflows(
    db: Session, *, bot: BusinessBot, bindings: dict, query: str,
    actor_user_id: str, runtime_run_id: str, selected_actions: list[dict] | None = None,
    auth_context: AuthContext | None = None,
) -> list[dict]:
    """保留客户端约束，通过正常运行入口执行本次选中的工作流。"""
    # 执行被选中的绑定工作流，返回工作流执行类证据（type="workflow_execution"）。
    #
    # 以 Bot 所有者身份创建运行（与动作选择的身份一致），未选中的工作流不会执行。
    # 副作用：真实触发工作流执行（这是本模块唯一会改动外部业务数据的路径）、写 runtime 记录与审计。
    #
    if not bindings.get("workflows") or not selected_actions:
        return []
    from app.modules.workflows.execution import start
    from app.modules.workflows.schemas import RunInput

    results = []
    for selected in selected_actions:
        if selected.get("kind") != "workflow":
            continue
        binding = selected["binding"]
        ctx, workflow, release, run_mode = _resolve_bound_workflow(
            db, bot=bot, binding=binding,
            actor_user_id=actor_user_id, runtime_run_id=runtime_run_id,
            auth_context=auth_context,
        )
        arguments = selected.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValidationError(f"Bot 工作流“{workflow.name}”的参数必须是 JSON 对象")
        graph = release.snapshot.get("graph") if isinstance(release.snapshot, dict) else {}
        start_node = next((node for node in (graph or {}).get("nodes", [])
                           if node.get("kind") == "start"), None)
        variables = ((start_node or {}).get("config") or {}).get("variables") or []
        declared = {
            str(item.get("variable")) for item in variables
            if isinstance(item, dict) and item.get("variable")
        }
        if variables:
            unexpected = sorted(set(arguments) - declared)
            if unexpected:
                raise ValidationError(
                    f"Bot 工作流“{workflow.name}”包含未定义开始参数：{', '.join(unexpected[:10])}"
                )
            required = {
                str(item.get("variable")) for item in variables
                if isinstance(item, dict) and item.get("variable") and item.get("required")
            }
            missing = sorted(name for name in required if arguments.get(name) is None)
            if missing:
                raise ValidationError(
                    f"Bot 工作流“{workflow.name}”缺少必填开始参数：{', '.join(missing[:10])}"
                )
            input_data = arguments or _workflow_inputs(release.snapshot, query)
        else:
            unexpected = sorted(set(arguments) - {"input"})
            if unexpected:
                raise ValidationError(
                    f"Bot 工作流“{workflow.name}”包含未定义开始参数：{', '.join(unexpected[:10])}"
                )
            input_data = arguments or {"input": query}
        pinned_revision = binding.get("revision")
        execution = start(
            ctx, workflow.id,
            RunInput(
                version=workflow.version,
                mode=run_mode,
                inputs=input_data,
                idempotencyKey=f"bot_{runtime_run_id}_{workflow.id}"[:100],
            ),
            published_revision=(int(pinned_revision) if pinned_revision is not None else None),
        )
        result = {
            "type": "workflow_execution",
            "workflowId": workflow.id,
            "workflowName": workflow.name,
            "executionId": execution.id,
            "runId": execution.runId,
            "revision": execution.revision,
            "status": execution.status,
            "errorMessage": execution.errorMessage,
            "result": _bounded_action_value(execution.result),
            "modelCallId": selected.get("model_call_id"),
        }
        results.append(result)
        if execution.status not in {"completed", "waiting"}:
            raise ValidationError(
                f"Bot 绑定工作流“{workflow.name}”执行失败：{execution.errorMessage or '请查看工作流运行记录'}"
            )
    return results


def _bound_tool_spec(db: Session, workspace_id: str, binding: dict, actor_user_id: str, run_id: str,
                     *, auth_context: AuthContext | None = None):
    """先校验执行权限，再读取固定历史正式版本或当前草稿版本。"""
    # 把绑定的工具解析成 ToolSpec，并做归属/授权校验（要求该工具已发布）。
    #
    # 返回 None 表示该绑定当前不可执行，由调用方转为 action_guidance 提示。
    #
    tool_id = str(binding.get("id") or "")
    version_id = str(binding.get("versionId") or "")
    # 先绑定执行范围再查询，查看权限的收窄不能误删已授权执行的资源。
    ctx = _bot_context(db, workspace_id, actor_user_id, "connectors.execute", run_id=run_id,
                       auth_context=auth_context)
    statement = select(Tool, ToolVersion, Connector, ConnectorVersion)
    # 固定历史版本按自身归属解析；源资源显式停用仍拒绝，新发布不使旧快照失效。
    statement = statement.join(ToolVersion, ToolVersion.tool_id == Tool.id)
    statement = statement.join(Connector, Connector.id == Tool.connector_id)
    statement = statement.join(ConnectorVersion, ConnectorVersion.id == ToolVersion.connector_version_id)
    statement = statement.where(
        Tool.workspace_id == workspace_id, Tool.id == tool_id,
        ToolVersion.tool_id == Tool.id, Connector.workspace_id == workspace_id,
        ConnectorVersion.connector_id == Connector.id,
    )
    if version_id:
        statement = statement.where(
            ToolVersion.id == version_id,
            Tool.status == "published",
            ToolVersion.status == "published",
            Connector.status == "published",
            ConnectorVersion.status == "published",
        )
    else:
        statement = statement.where(
            Tool.status.notin_(("deleted", "archived")),
            ToolVersion.status.notin_(("deleted", "archived")),
            Connector.status.notin_(("deleted", "archived")),
            ConnectorVersion.status.notin_(("deleted", "archived")),
        ).order_by(ToolVersion.version_number.desc(), ConnectorVersion.version_number.desc())
    row = db.execute(statement).first()
    if row is None:
        raise ValidationError("Bot 绑定的工具已停用、未发布或正式版本发生变化")
    tool, tool_version, connector, connector_version = row
    require_resource(ctx, "connectors.execute", connector)
    spec = ToolSpec.from_dict(tool_version.spec or {})
    spec.connector_id = connector.id
    spec.connector_version_id = connector_version.id
    spec.provider = connector.provider
    spec._connector_spec = connector_version.spec or {}  # type: ignore[attr-defined]
    spec._tool_id = tool.id  # type: ignore[attr-defined]
    return ctx, tool, tool_version, connector_version, spec


def _tool_arguments(spec: ToolSpec, query: str) -> dict:
    """Map a Bot message only when the bound tool has an unambiguous input."""
    # 根据 ToolSpec 的参数 schema 与用户问题构造工具入参。
    #
    # 只填必填参数、且尽量把问题原文作为输入，缺参时返回空 dict 让网关侧拒绝执行——
    # 宁可判为"参数不足、未执行"，也不要猜参数导致误操作。
    #
    schema = spec.input_schema or {"type": "object", "properties": {}}
    properties = schema.get("properties") or {}
    required = list(schema.get("required") or [])
    if not properties:
        return {}
    for name in ("input", "query", "message", "text"):
        if name in properties and (not required or name in required):
            return {name: query}
    if len(required) == 1 and required[0] in properties:
        prop = properties[required[0]] or {}
        if prop.get("type") in {None, "string"}:
            return {required[0]: query}
    if len(properties) == 1:
        name, prop = next(iter(properties.items()))
        if not required or name in required:
            if (prop or {}).get("type") in {None, "string"}:
                return {name: query}
    raise ValidationError(f"Bot 绑定工具“{spec.name}”需要多个参数，无法从一条消息安全推断")


def _execute_bound_tools(
    db: Session, *, bot: BusinessBot, bindings: dict, query: str,
    actor_user_id: str, runtime_run_id: str, selected_actions: list[dict] | None = None,
    auth_context: AuthContext | None = None,
) -> list[dict]:
    """携带受限身份，经 ToolGateway 执行本次选中的绑定工具。"""
    # 执行被选中的绑定工具，返回工具执行类证据（type="tool_execution"）。
    #
    # 通过 ToolGateway 统一执行，因此继承网关的权限、超时与审计行为。
    # 副作用：真实调用外部工具/连接器，可能产生写操作与费用。
    #
    if not bindings.get("tools") or not selected_actions:
        return []
    from app.modules.runtime import service as runtime

    results = []
    sequence = 100
    selected_by_binding = {
        str(item.get("binding", {}).get("id") or ""): item
        for item in selected_actions if item.get("kind") == "tool"
    }
    for binding in bindings.get("tools", []):
        selected = selected_by_binding.get(str(binding.get("id") or ""))
        if selected is None:
            continue
        sequence += 1
        ctx, tool, tool_version, connector_version, spec = _bound_tool_spec(
            db, bot.workspace_id, binding, actor_user_id, runtime_run_id,
            auth_context=auth_context,
        )
        if selected.get("name") != spec.name:
            raise ValidationError("Bot 绑定工具版本的名称已变化，请重新发布")
        if spec.requires_approval:
            raise ValidationError(f"Bot 绑定工具“{spec.name}”需要人工批准后才能执行")
        arguments = selected.get("arguments")
        if not isinstance(arguments, dict):
            raise ValidationError(f"Bot 绑定工具“{spec.name}”的参数必须是 JSON 对象")
        step = runtime.start_step(
            db, workspace_id=bot.workspace_id, run_id=runtime_run_id,
            sequence=sequence, step_type="bot_tool", name=spec.name,
            input_data=arguments,
        )
        call = runtime.record_tool_call(
            db, workspace_id=bot.workspace_id, run_id=runtime_run_id, step_id=step.id,
            tool_name=spec.name, tool_version_id=tool_version.id, arguments=arguments,
        )
        # Bot 调试/运行的同一节点重试复用调用键，避免 HTTP/MCP 工具重复产生外部副作用。
        call.idempotency_key = "bot-" + hashlib.sha256(
            f"{runtime_run_id}:{sequence}:{spec.connector_version_id}:{spec.name}".encode("utf-8")
        ).hexdigest()
        db.commit()
        execution = build_context(
            db=db, workspace_id=bot.workspace_id, user_id=actor_user_id,
            run_id=runtime_run_id, conversation_id="",
            allow_network=spec.provider in {"http", "mcp"},
        )
        execution["idempotency_key"] = call.idempotency_key
        try:
            execution["auth_context"] = _bot_context(db, bot.workspace_id, actor_user_id,
                "connectors.execute", run_id=runtime_run_id, auth_context=auth_context)
            result = ToolGateway(db).execute_spec(
                spec, arguments, connector_version.spec or {}, execution,
            )
            output = result.to_dict()
            if result.status == "failed":
                runtime.fail_step(db, step, result.error_code or "tool_execution_error",
                                  result.error_summary or "工具执行失败")
                runtime.finish_tool_call(db, call, output, result.error_code, result.error_summary)
            else:
                runtime.complete_step(db, step, output)
                runtime.finish_tool_call(db, call, output)
            db.commit()
        except Exception as exc:
            db.rollback()
            runtime.fail_step(db, step, "tool_execution_error", str(exc))
            runtime.finish_tool_call(db, call, {"status": "failed", "error_summary": str(exc)},
                                     "tool_execution_error", str(exc))
            db.commit()
            raise
        evidence = {
            "type": "tool_execution",
            "toolId": tool.id,
            "toolName": spec.name,
            "toolVersionId": tool_version.id,
            "connectorVersionId": connector_version.id,
            "toolCallId": call.id,
            "status": result.status,
            "data": _bounded_action_value(result.data),
            "metadata": _bounded_action_value(result.metadata),
            "errorCode": result.error_code,
            "errorSummary": result.error_summary,
            "modelCallId": selected.get("model_call_id"),
        }
        results.append(evidence)
        if result.status == "failed":
            raise ValidationError(
                f"Bot 绑定工具“{spec.name}”执行失败：{result.error_summary or '请查看运行记录'}"
            )
    return results


def _skill_instructions(
    db: Session, *, bot: BusinessBot, bindings: dict, actor_user_id: str, runtime_run_id: str,
    auth_context: AuthContext | None = None,
) -> tuple[list[dict], str]:
    """核对当前 Skill 授权与状态，应用固定版本指令并记录来源。"""
    # 加载被绑定的已发布 Skill 指令，返回 (skill 证据列表, 合并后的指令文本)。
    #
    # 只有已发布版本会被应用；指令文本会拼进模型 system 提示，让 Bot 的回答遵循企业方法论。
    # 副作用：写 skill 应用类证据（type="skill_execution"）并 commit。
    #
    if not bindings.get("skills"):
        return [], ""
    from app.modules.runtime import service as runtime

    evidence, instructions = [], []
    for index, binding in enumerate(bindings.get("skills", []), start=1):
        skill_id = str(binding.get("id") or "")
        version_id = str(binding.get("versionId") or "")
        ctx = _bot_context(db, bot.workspace_id, actor_user_id, "skills.view", run_id=runtime_run_id,
                           auth_context=auth_context)
        if version_id:
            skill = ctx.db.scalar(select(Skill).where(
                Skill.workspace_id == bot.workspace_id, Skill.id == skill_id,
                Skill.status == "published",
            ))
            version = ctx.db.scalar(select(SkillVersion).where(
                SkillVersion.id == version_id, SkillVersion.skill_id == skill_id,
                SkillVersion.status == "published",
            ))
        else:
            skill = ctx.db.scalar(select(Skill).where(
                Skill.workspace_id == bot.workspace_id, Skill.id == skill_id,
                Skill.status.notin_(("deleted", "archived")),
            ))
            version = ctx.db.scalar(select(SkillVersion).where(
                SkillVersion.skill_id == skill_id,
            ).order_by(SkillVersion.version_number.desc()).limit(1)) if skill else None
        if skill is None or version is None:
            raise ValidationError("Bot 绑定的 Skill 已停用或正式版本发生变化")
        require_resource(ctx, "skills.view", skill)
        spec = version.spec or {}
        text = str(spec.get("instructions") or spec.get("description") or "").strip()
        if text:
            instructions.append(f"方法 {skill.name}（固定版本 {version.id}）：\n{text[:6000]}")
        step = runtime.start_step(
            db, workspace_id=bot.workspace_id, run_id=runtime_run_id,
            sequence=index, step_type="bot_skill", name=skill.name,
            input_data={"skillId": skill.id, "skillVersionId": version.id},
        )
        runtime.complete_step(db, step, {"skillId": skill.id, "skillVersionId": version.id, "applied": bool(text)})
        evidence.append({
            "type": "skill_execution", "skillId": skill.id,
            "skillName": skill.name, "skillVersionId": version.id,
            "status": "applied", "instructionApplied": bool(text),
        })
    db.commit()
    return evidence, "\n\n".join(instructions)


def _retrieval_answer(references: list[dict]) -> str:
    """非模型模式（mode != "model"）的回答：把证据整理成编号清单，不调用大模型。

    用途：没有模型可用、或只想看"召回了什么"时使用；输出是可复现的纯模板文本。
    bot_runtime 类型的内部引用会被过滤掉（它只是运行标识，不是证据），
    最多展示前 6 条，知识分块正文压平空白后截断到 260 字——控制答案长度。
    没有任何可展示证据时返回固定的"未找到相关内容"文案（明确说明没有硬答）。
    """
    visible_references = [
        reference for reference in references if reference.get("type") != "bot_runtime"
    ]
    if not visible_references:
        return "当前绑定的知识库和图谱中未找到与问题直接相关的内容。"
    lines = []
    index = 0
    for reference in visible_references:
        index += 1
        if index > 6:
            break
        if reference["type"] == "knowledge_chunk":
            content = " ".join(reference["content"].split())
            lines.append(f"{index}. 文档证据：{content[:260]}")
        elif reference.get("type") == "graph_entity":
            neighbors = "、".join(item.get("entityName") or item.get("entityId", "") for item in reference["neighbors"][:4])
            suffix = f"；关联：{neighbors}" if neighbors else ""
            lines.append(f"{index}. 图谱实体：{reference.get('name')}（{reference.get('businessKey')}）{suffix}")
        elif reference.get("type") == "workflow_execution":
            lines.append(
                f"{index}. 工作流执行：{reference.get('workflowName')}，状态 {reference.get('status')}"
            )
        elif reference.get("type") == "tool_execution":
            lines.append(
                f"{index}. 工具执行：{reference.get('toolName')}，状态 {reference.get('status')}"
            )
        elif reference.get("type") == "skill_execution":
            lines.append(f"{index}. 已应用方法：{reference.get('skillName')}")
        elif reference.get("type") == "action_guidance":
            lines.append(f"{index}. 本次未执行动作：{reference.get('content')}")
    return "\n".join(lines)


def _model_answer(
    configuration: dict, query: str, references: list[dict], workspace_id: str,
    *, skill_instructions: str = "", runtime_run_id: str | None = None,
) -> str:
    """模型模式回答：把证据 JSON 交给模型，要求逐条引用编号，禁止编造证据外的事实。

    防幻觉约束都在 system 提示里：只能依据给定证据、action_guidance 不等于执行成功、
    关键结论必须标 [1][2] 对应证据序号、证据不足要明说。temperature=0.1 追求稳定复现。
    无证据时不调用模型（直接返回"未调用模型生成无依据答案"），既省钱也避免凭空生成。
    参数：skill_instructions 是已启用方法的指令文本，会被拼进 system 提示。
    异常：ModelError —— 模型返回空内容。
    副作用：调用模型（按 workspace 计费，usage_context 记为 source=bot），并可能写入模型用量记录。
    """
    if not references:
        return "当前绑定的知识库和图谱中未找到与问题直接相关的内容，未调用模型生成无依据答案。"
    answer_references = [
        reference for reference in references if reference.get("type") != "bot_runtime"
    ]
    if not answer_references:
        return "本次 Bot 运行没有产生可供回答的证据。"
    source_text = json.dumps(answer_references, ensure_ascii=False)
    response = model.chat_for_run(
        configuration.get("modelProfile") or "chat",
        [
            {
                "role": "system",
                "content": (
                    "你是企业业务 Bot。只能依据给定证据回答；不能补充证据中不存在的事实。"
                    "action_guidance 仅表示未执行动作的说明或待补充参数，不能据此声称已经执行成功。"
                    "每个关键结论用 [1]、[2] 标注对应证据序号；证据不足时明确说明。\n"
                    + (configuration.get("instructions") or "")
                    + ("\n\n已绑定并启用的方法：\n" + skill_instructions if skill_instructions else "")
                ),
            },
            {"role": "user", "content": f"问题：{query}\n证据：{source_text}"},
        ],
        temperature=0.1,
        workspace_id=workspace_id,
        usage_context={
            "source": "bot",
            "source_id": runtime_run_id,
        } if runtime_run_id else {"source": "bot"},
    )
    answer = (response.get("content") or "").strip()
    if not answer:
        raise ModelError("模型未返回回答")
    return answer


def _run_bot(db: Session, *, bot: BusinessBot, revision: BotRevision | None, query: str,
             mode: str, channel: str, actor_user_id: str,
             auth_context: AuthContext | None = None) -> BotDebugRun:
    """Bot 运行主流程，返回（并已写库）本次 BotDebugRun 记录。调试与公开 API 共用它。

    参数：revision 为 None 走当前草稿（仅调试），有值走冻结快照；mode 决定"模型回答"还是
        "证据清单"（见 _retrieval_answer / _model_answer）；channel 区分 debug / api 等入口。
    阶段顺序（同一数据库会话被复用，每个阶段都要重新绑定自己的数据范围，否则会串权限）：
        1. 建立 bots.execute 作用域的 service 身份（公开 Key 不冻结创建者权限，
           先解析身份，才能让成员/角色/授权的变更实时生效）；
        2. 先落一条 status="running" 的运行记录并 commit——即使后续崩溃，也有痕迹可查；
        3. 创建 runtime 运行（用于统一计费与运行观测），并把 bot_runtime 引用放进证据链；
        4. 分阶段取证：知识库（knowledge.view）→ 图谱（graphs.view）→ Skill → 动作选择 →
           工作流执行 → 工具执行，每一步都用各自的 _bot_context 重建身份；
        5. 写回证据并生成回答；最后把 run 置 completed 并结束 runtime。
    异常处理：任何异常都会回滚数据库、重新绑定 bots.execute 作用域（回滚会清掉加载器条件，
        不重绑就取不到本行数据）、把 runtime 与 run 双双标记 failed 并记录截断到 4000 字的错误，
        最后原样抛出——失败信息必须让用户看到，不能被吞掉。
    """
    configuration, bindings = _configuration(bot, revision)
    query = query.strip()
    if not query:
        raise ValidationError("Bot 问题不能为空")
    from app.modules.runtime import service as runtime

    # A public key does not freeze the creator's authority.  Resolve the
    # service identity before creating a run so revocation/removal takes effect.
    bot_scope_ctx = _bot_context(
        db, bot.workspace_id, actor_user_id, "bots.execute", run_id="bot-authorization",
        auth_context=auth_context,
    )
    require_resource(bot_scope_ctx, "bots.execute", bot)
    started = time.perf_counter()
    run = BotDebugRun(
        workspace_id=bot.workspace_id,
        owner_user_id=actor_user_id,
        bot_id=bot.id,
        bot_revision=revision.revision if revision else None,
        channel=channel,
        mode=mode,
        query=query,
        answer="",
        references=[],
        status="running",
        error_message="",
        duration_ms=0,
    )
    db.add(run)
    db.commit()
    runtime_run = None
    action_references: list[dict] = []
    try:
        runtime_run = runtime.create_run(
            db,
            workspace_id=bot.workspace_id,
            conversation_id="",
            user_id=actor_user_id,
            context_snapshot={
                "entry": "business_bot",
                "bot_id": bot.id,
                "bot_revision": revision.revision if revision else None,
            },
            model_profile=configuration.get("modelProfile") or "chat",
        )
        runtime_run.status = "running"
        runtime_run.started_at = datetime.utcnow()
        db.commit()
        action_references.append({"type": "bot_runtime", "runId": runtime_run.id})
        # Each phase rebinds the session to its exact permission.  This keeps
        # a public API key subject to live membership, role and grant changes.
        knowledge_ctx = (
            _bot_context(
                db, bot.workspace_id, actor_user_id, "knowledge.view", run_id=runtime_run.id,
                auth_context=auth_context,
            )
            if bindings.get("knowledgeBases") else None
        )
        graph_ctx = (
            _bot_context(
                db, bot.workspace_id, actor_user_id, "graphs.view", run_id=runtime_run.id,
                auth_context=auth_context,
            )
            if bindings.get("graphs") else None
        )
        references = _grounded_references(
            db, bot.workspace_id, bindings, query,
            auth_ctx=knowledge_ctx, graph_auth_ctx=graph_ctx,
        )
        skill_references, skill_instructions = _skill_instructions(
            db, bot=bot, bindings=bindings, actor_user_id=actor_user_id,
            runtime_run_id=runtime_run.id,
            auth_context=auth_context,
        )
        action_references.extend(skill_references)
        action_feedback: list[str] = []
        selected_actions = _select_bound_actions(
            db, bot=bot, bindings=bindings, query=query,
            configuration=configuration, actor_user_id=actor_user_id,
            runtime_run_id=runtime_run.id,
            feedback=action_feedback,
            auth_context=auth_context,
        )
        action_references.extend({"type": "action_guidance", "content": text} for text in action_feedback)
        action_references.extend(_execute_bound_workflows(
            db, bot=bot, bindings=bindings, query=query,
            actor_user_id=actor_user_id, runtime_run_id=runtime_run.id,
            selected_actions=selected_actions,
            auth_context=auth_context,
        ))
        action_references.extend(_execute_bound_tools(
            db, bot=bot, bindings=bindings, query=query,
            actor_user_id=actor_user_id, runtime_run_id=runtime_run.id,
            selected_actions=selected_actions,
            auth_context=auth_context,
        ))
        references = [*references, *action_references]
        # Knowledge, graph, Skill and connector phases share one session and
        # each installs its own loader criteria.  Restore the entry-point
        # scope before refreshing and writing the Bot result.
        bot_scope_ctx = _bot_context(db, bot.workspace_id, actor_user_id, "bots.execute",
            run_id=runtime_run.id, auth_context=auth_context)
        require_resource(bot_scope_ctx, "bots.execute", bot)
        db.refresh(run)
        run.references = references
        db.commit()
        answer = (_model_answer(
            configuration, query, references, bot.workspace_id,
            skill_instructions=skill_instructions, runtime_run_id=runtime_run.id,
        ) if mode == "model" else _retrieval_answer(references))
        run.references = references
        run.answer = answer
        run.status = "completed"
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        runtime.finalize_run(db, runtime_run, status="completed")
        db.commit()
        return run
    except Exception as exc:
        db.rollback()
        bind_data_scope(bot_scope_ctx, "bots.execute")
        run = db.get(BotDebugRun, run.id)
        if runtime_run is not None:
            runtime.finalize_run(
                db, runtime_run, status="failed",
                error_code=type(exc).__name__, error_summary=str(exc),
            )
        run.status = "failed"
        run.references = action_references
        run.error_message = str(exc)[:4000]
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        db.commit()
        raise


def debug_bot(ctx: AuthContext, bot_id: str, payload: BotDebugInput) -> dict:
    """调试运行 Bot（要求 bots.execute），返回运行记录视图。

    payload.use_published 决定跑正式版（冻结快照）还是当前草稿；跑草稿前会先做一次绑定校验，
    让"草稿里绑了已失效资源"在开始运行前就报错，而不是运行中途失败。
    副作用：写 bot_debug_runs 与 runtime 记录、调用模型/工作流/工具，并写审计 action=bots.debug。
    异常：NotFoundError（Bot 不存在）、ValidationError（草稿绑定非法）、ConflictError、
        以及运行期由模型/工作流/工具抛出的错误。
    """
    bot = get_bot(ctx, bot_id, "bots.execute")
    if payload.use_published:
        revision = _revision(ctx, bot)
    else:
        _validate_bindings(ctx, _payload_from_bot(bot))
        revision = None
    run = _run_bot(
        ctx.db, bot=bot, revision=revision, query=payload.query.strip(), mode=payload.mode,
        channel="debug", actor_user_id=ctx.user_id,
        auth_context=ctx,
    )
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="bots.debug", resource_type="business_bot", resource_id=bot.id,
          metadata={"botRunId": run.id, "mode": payload.mode, "revision": run.bot_revision})
    ctx.db.commit()
    return debug_run_view(run)


def list_debug_runs(ctx: AuthContext, bot_id: str, *, limit: int = 100) -> list[dict]:
    """列出该 Bot 的运行记录（倒序，最多 limit 条）。异常：NotFoundError（Bot 不存在）。"""
    bot = get_bot(ctx, bot_id, "bots.view")
    rows = ctx.db.scalars(select(BotDebugRun).where(
        BotDebugRun.workspace_id == ctx.workspace_id,
        BotDebugRun.bot_id == bot.id,
    ).order_by(BotDebugRun.created_at.desc()).limit(limit)).all()
    return [debug_run_view(row) for row in rows]


def create_api_key(ctx: AuthContext, bot_id: str, payload: BotApiKeyInput) -> dict:
    """为已发布的 Bot 创建 API Key（要求 bots.publish），返回带明文密钥的视图。

    只存摘要：库中保存 key_digest = sha256(明文)，明文仅在本响应里返回一次（shownOnce=True），
    之后任何接口都无法再取回；key_prefix 取明文前 12 字符用于展示与快速定位。
    前置条件：Bot 必须已发布且有 published_revision，且 api_enabled 为真——未发布就发 Key 会让
    外部系统调用到一个语义未冻结的 Bot。
    时间处理：带时区的 expires_at 会统一转成 UTC 的 naive 时间（库里统一按 naive UTC 存），
    并拒绝早于当前时间的过期时间。
    参数：密钥格式固定为 "t5b_" + token_urlsafe(32)。
    异常：ValidationError（未发布/未启用/过期时间非法）、ConflictError（生成冲突）。
    副作用：写 bot_api_keys，写审计（只记 key_prefix，不记明文）。
    """
    bot = get_bot(ctx, bot_id, "bots.publish")
    if bot.status != "published" or bot.published_revision is None:
        raise ValidationError("请先发布 Bot 后再创建 API Key")
    if not bot.api_enabled:
        raise ValidationError("Bot 尚未启用 API 调用")
    expires_at = payload.expires_at
    if expires_at and expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    if expires_at and expires_at <= datetime.utcnow():
        raise ValidationError("API Key 过期时间必须晚于当前时间")
    plaintext = "t5b_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    row = BotApiKey(
        workspace_id=ctx.workspace_id,
        owner_user_id=ctx.user_id,
        bot_id=bot.id,
        name=payload.name.strip(),
        key_prefix=plaintext[:12],
        key_digest=digest,
        status="active",
        expires_at=expires_at,
    )
    ctx.db.add(row)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="bots.api_key.create", resource_type="bot_api_key", resource_id=row.id,
          metadata={"bot_id": bot.id, "key_prefix": row.key_prefix})
    _commit(ctx, "API Key 生成冲突，请重试")
    return {**api_key_view(row), "key": plaintext, "shownOnce": True}


def list_api_keys(ctx: AuthContext, bot_id: str) -> list[dict]:
    """列出该 Bot 的 API Key（倒序，最多 200 条）；不返回密钥本体。异常：NotFoundError。"""
    bot = get_bot(ctx, bot_id, "bots.view")
    rows = ctx.db.scalars(select(BotApiKey).where(
        BotApiKey.workspace_id == ctx.workspace_id,
        BotApiKey.bot_id == bot.id,
    ).order_by(BotApiKey.created_at.desc()).limit(200)).all()
    return [api_key_view(row) for row in rows]


def revoke_api_key(ctx: AuthContext, bot_id: str, key_id: str, version: int) -> dict:
    """撤销一把 API Key（要求 bots.publish），返回撤销后的视图。

    三重归属校验（key 属于该工作区且属于该 Bot）后再做乐观锁校验，避免跨 Bot 误撤。
    撤销只改状态为 "revoked"，不删行——保留"谁在什么时候用过这把钥匙"的审计线索。
    异常：NotFoundError（Key 不存在或不属于该 Bot）、ConflictError（版本冲突/提交失败）。
    """
    bot = get_bot(ctx, bot_id, "bots.publish")
    row = ctx.db.get(BotApiKey, key_id)
    if not row or row.workspace_id != ctx.workspace_id or row.bot_id != bot.id:
        raise NotFoundError("API Key 不存在")
    _version(row, version)
    row.status = "revoked"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="bots.api_key.revoke", resource_type="bot_api_key", resource_id=row.id,
          metadata={"bot_id": bot.id, "key_prefix": row.key_prefix})
    _commit(ctx, "API Key 吊销失败")
    return api_key_view(row)


def invoke_public(db: Session, *, bot_id: str, api_key: str, query: str, mode: str) -> dict:
    """对外 API 入口：用 API Key 调用某个已发布 Bot，返回 {botId, revision, runId, answer, references, durationMs}。

    鉴权与信息泄露防护：所有失败情况（前缀/长度不对、摘要不匹配、Key 已撤销或过期、
    Bot 未发布/未启用 API/正式修订缺失）都返回同一句 "Bot API 不存在或凭据无效"，
    让外部无法通过错误文案区分"bot 不存在"与"key 错误"。
    比对方式：先按 (bot_id, key_prefix) 缩小候选再逐条 hmac.compare_digest 比对摘要（防时序侧信道），
    因此库里的 key_digest 必须是 sha256 十六进制字符串。
    运行身份：以 bot.owner_user_id 作为 actor——公开调用不携带调用者身份，
    权限由 Bot 所有者的实时角色/授权决定（见 _run_bot 的身份解析说明）。
    异常：NotFoundError（统一文案）以及运行期由模型/工作流/工具抛出的错误。
    副作用：更新 key_row.last_used_at，写运行记录与审计 action=bots.api.invoke（actor_user_id 为空）。
    """
    if not api_key.startswith("t5b_") or len(api_key) < 24:
        raise NotFoundError("Bot API 不存在或凭据无效")
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    candidates = db.scalars(select(BotApiKey).where(
        BotApiKey.bot_id == bot_id,
        BotApiKey.key_prefix == api_key[:12],
        BotApiKey.status == "active",
    )).all()
    key_row = next((row for row in candidates if hmac.compare_digest(row.key_digest, digest)), None)
    now = datetime.utcnow()
    if not key_row or (key_row.expires_at and key_row.expires_at <= now):
        raise NotFoundError("Bot API 不存在或凭据无效")
    bot = db.get(BusinessBot, bot_id)
    if (not bot or bot.workspace_id != key_row.workspace_id or bot.status != "published"
            or not bot.api_enabled or bot.published_revision is None):
        raise NotFoundError("Bot API 不存在或凭据无效")
    revision = db.scalar(select(BotRevision).where(
        BotRevision.workspace_id == bot.workspace_id,
        BotRevision.bot_id == bot.id,
        BotRevision.revision == bot.published_revision,
    ))
    if not revision:
        raise NotFoundError("Bot API 不存在或凭据无效")
    key_row.last_used_at = now
    db.commit()
    run = _run_bot(
        db, bot=bot, revision=revision, query=query.strip(), mode=mode,
        channel="api", actor_user_id=bot.owner_user_id,
    )
    audit(db, workspace_id=bot.workspace_id, actor_user_id=None,
          action="bots.api.invoke", resource_type="business_bot", resource_id=bot.id,
          metadata={"botRunId": run.id, "mode": mode, "key_prefix": key_row.key_prefix, "revision": revision.revision})
    db.commit()
    return {
        "botId": bot.id,
        "revision": revision.revision,
        "runId": run.id,
        "answer": run.answer,
        "references": run.references,
        "durationMs": run.duration_ms,
    }
