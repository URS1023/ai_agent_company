# -*- coding: utf-8 -*-
"""Plugin service — business capability package = Skill(s) + Tools + Resource bindings.

中文补充（插件服务）：
- Plugin = 能力包：一组 Skill（方法）+ 工具引用 + 资源绑定（知识库/数据源/文件）,
  由 AI 会话按 plugin_version_id 挂载使用。
- 版本与绑定：PluginVersion 不可变；创建/改版时把 spec 中的引用**冻结**成具体版本 ID
  （skill_version_id / tool_version_id）并写入三张绑定表（Plugin*Binding），
  因此后续源技能/工具升级不会悄悄改变已发布插件的行为。
- 引用解析规则（_build_spec）：技能取"已发布版本，缺失则回退最新版本"；
  工具只认"已发布工具版本"或内置工具名，解析不到的引用会被丢弃（不写进 spec），
  避免出现指向不存在/未发布工具的悬空绑定。
- 生命周期：create_plugin(draft) → new_draft_version → publish → clone/share/archive；
  test_run 会真实跑一次 agent（可产生会话、工具调用与工件）。
- 权限：本服务只做工作区隔离，资源级鉴权由 router 层承担。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import NotFoundError, ValidationError
from ...ids import new_id
from ...models import (Plugin, PluginResourceBinding, PluginSkillBinding,
                       PluginToolBinding, PluginVersion, SkillVersion,
                       Tool as ToolRow, ToolVersion, ToolCall)
from ..skill.service import latest_version as latest_skill_version


def _now():
    """统一的 UTC 写入时间。"""
    return datetime.utcnow()


def _resolve_skill_version(db, skill_id: str):
    """解析技能引用对应的版本：优先已发布版本，其次最新版本，技能不存在返回 None。

    优先已发布是刻意的：插件是"能直接跑起来的能力包"，绑到草稿技能上会随草稿变动。
    """
    from ...models import Skill
    s = db.get(Skill, skill_id)
    if not s:
        return None
    return s.published_version_id and db.get(SkillVersion, s.published_version_id) or latest_skill_version(db, skill_id)


def _resolve_tool_version(db, workspace_id: str, tool_name: str):
    """按工具名解析"已发布工具版本"；未发布或不存在返回 None（调用方会丢弃该引用）。

    只连 ToolVersion.id == Tool.published_version_id，因此草稿工具不会被绑定。
    """
    row = db.execute(select(ToolRow, ToolVersion)
                     .join(ToolVersion, ToolVersion.id == ToolRow.published_version_id)
                     .where(ToolRow.workspace_id == workspace_id, ToolRow.name == tool_name)
                     ).first()
    if row:
        return row[1]
    return None


def create_plugin(db: Session, *, workspace_id: str, owner_user_id: str, name: str,
                  description: str = "", spec: dict = None) -> dict:
    """创建 Plugin 及其第 1 个草稿版本与绑定记录（draft + workspace 可见）。

    返回 {"plugin_id","version_id","name"}；不 commit（调用方决定事务边界）。
    spec 会先经 _build_spec 冻结版本引用，再写三张绑定表。
    """
    spec = spec or {}
    plugin = Plugin(id=new_id(), workspace_id=workspace_id, owner_user_id=owner_user_id,
                    name=name, description=description, visibility="workspace", status="draft",
                    created_at=_now(), updated_at=_now())
    db.add(plugin)
    db.flush()
    pv = PluginVersion(id=new_id(), plugin_id=plugin.id, version_number=1, status="draft",
                       spec=_build_spec(db, workspace_id, spec, owner_user_id), created_by=owner_user_id,
                       created_at=_now())
    db.add(pv)
    db.flush()
    _add_bindings(db, pv, spec)
    return {"plugin_id": plugin.id, "version_id": pv.id, "name": name}


def _build_spec(db, workspace_id, spec: dict, user_id: str) -> dict:
    """把插件 spec 规范化：把技能/工具引用解析成冻结版本，丢弃解析不到的引用。

    兼容历史字段名（skill_ids / tool_ids、工具项写成 dict）。内置工具没有 Tool 行，
    因此带 provider=builtin 标记直接通过；连接器工具必须能解析到已发布版本。
    返回规范化后的 spec 字段（不含未识别键）。
    """
    skill_refs = []
    for sid in spec.get("skill_refs") or ((spec.get("skill_ids")) or []):
        sv = _resolve_skill_version(db, sid)
        if sv:
            skill_refs.append({"skill_id": sid, "skill_version_id": sv.id})
    tool_refs = []
    from ...providers.tool.builtin_provider import builtin_specs
    builtin_names = {item.name for item in builtin_specs()}
    for tname in spec.get("tool_refs") or ((spec.get("tool_ids")) or []):
        if isinstance(tname, dict):
            tname = tname.get("tool_name") or tname.get("name")
        if not tname:
            continue
        tv = _resolve_tool_version(db, workspace_id, tname)
        if tv:
            tool_refs.append({"tool_name": tname, "tool_version_id": tv.id})
        elif tname in builtin_names:
            tool_refs.append({"tool_name": tname, "provider": "builtin"})
    return {
        "name": spec.get("name") or spec.get("plugin_name") or "",
        "description": spec.get("description", ""),
        "instructions": spec.get("instructions", ""),
        "input_schema": spec.get("input_schema") or {},
        "output_schema": spec.get("output_schema") or {},
        "skill_refs": skill_refs,
        "tool_refs": tool_refs,
        "resource_bindings": spec.get("resource_bindings") or [],
        "permissions": spec.get("permissions") or {},
        "risk_level": spec.get("risk_level", "low"),
    }


def _add_bindings(db: Session, pv: PluginVersion, spec: dict) -> None:
    """把 spec 里的引用写成绑定行（便于按技能/工具反查哪些插件在用）。

    只有拿到具体 tool_version_id 的连接器工具才写 ToolBinding；内置工具无版本可绑，
    仅留在 spec 中。不 commit，由调用方统一提交。
    """
    for ref in (pv.spec or {}).get("skill_refs") or []:
        db.add(PluginSkillBinding(id=new_id(), plugin_version_id=pv.id,
                                  skill_version_id=ref["skill_version_id"]))
    for ref in (pv.spec or {}).get("tool_refs") or []:
        if ref.get("tool_version_id"):
            db.add(PluginToolBinding(id=new_id(), plugin_version_id=pv.id,
                                     tool_version_id=ref["tool_version_id"]))
    for rb in (pv.spec or {}).get("resource_bindings") or []:
        db.add(PluginResourceBinding(id=new_id(), plugin_version_id=pv.id,
                                     resource_type=rb.get("resource_type", "datasource"),
                                     resource_id=rb.get("resource_id", ""),
                                     config=rb.get("config") or {}))
    db.flush()


def list_plugins(db: Session, workspace_id: str):
    """列出本工作区未归档的 Plugin（按创建时间倒序）。"""
    return db.execute(select(Plugin).where(Plugin.workspace_id == workspace_id, Plugin.status != "archived")
                      .order_by(Plugin.created_at.desc())).scalars().all()


def get_plugin(db: Session, workspace_id: str, plugin_id: str) -> Plugin:
    """按 ID 取 Plugin 并校验工作区归属；不存在或跨工作区统一抛 NotFoundError。"""
    row = db.get(Plugin, plugin_id)
    if not row or row.workspace_id != workspace_id:
        raise NotFoundError("Plugin 不存在")
    return row


def latest_version(db: Session, plugin_id: str) -> PluginVersion:
    """取最新版本（version_number 最大）；无版本返回 None。"""
    return db.execute(select(PluginVersion).where(PluginVersion.plugin_id == plugin_id)
                      .order_by(PluginVersion.version_number.desc()).limit(1)).scalar_one_or_none()


def new_draft_version(db: Session, plugin_id: str, user_id: str, spec: dict, workspace_id: str) -> PluginVersion:
    """追加新草稿版本：在旧 spec 基础上做浅合并（未提及的字段保持原样）。

    浅合并的意义：能力修改只需要提供变化的字段，避免模型漏字段导致绑定被清空；
    name/description 同步到主记录（长度上限 200/1000，超限抛 ValidationError）。
    副作用：写版本与绑定表、把 Plugin 状态退回 draft，并 commit。
    """
    plugin = db.get(Plugin, plugin_id)
    if not plugin:
        raise NotFoundError("Plugin 不存在")
    if "name" in spec:
        name = str(spec["name"] or "").strip()
        if not name or len(name) > 200:
            raise ValidationError("Plugin 名称需为 1 至 200 个字符")
        plugin.name = name
    if "description" in spec:
        description = str(spec["description"] or "").strip()
        if len(description) > 1000:
            raise ValidationError("Plugin 用途不能超过 1000 个字符")
        plugin.description = description
    lv = latest_version(db, plugin_id)
    next_ver = (lv.version_number + 1) if lv else 1
    merged = dict(lv.spec or {}) if lv else {}
    merged.update(_build_spec(db, workspace_id, spec, user_id))
    nv = PluginVersion(id=new_id(), plugin_id=plugin_id, version_number=next_ver,
                       based_on_version_id=lv.id if lv else None, status="draft",
                       spec=merged, created_by=user_id, created_at=_now())
    db.add(nv)
    db.flush()
    _add_bindings(db, nv, merged)
    plugin.status = "draft"
    plugin.updated_at = _now()
    db.commit()
    return nv


def publish(db: Session, plugin_id: str) -> PluginVersion:
    """发布最新版本并让 Plugin 指向它；无版本抛 ValidationError。内部 commit。"""
    plugin = db.get(Plugin, plugin_id)
    if not plugin:
        raise NotFoundError("Plugin 不存在")
    lv = latest_version(db, plugin_id)
    if not lv:
        raise ValidationError("Plugin 没有版本")
    lv.status = "published"
    lv.published_at = _now()
    plugin.status = "published"
    plugin.published_version_id = lv.id
    plugin.updated_at = _now()
    db.commit()
    return lv


def clone(db: Session, *, workspace_id: str, user_id: str, plugin_id: str, new_name: str = "") -> dict:
    """复制 Plugin（含最新版本 spec 与绑定）为独立 private 草稿。

    副本沿用源 spec 里已冻结的版本引用（不重新解析），因此行为与源版本一致；
    返回 {"plugin_id","version_id","name"}，不 commit。
    """
    src = get_plugin(db, workspace_id, plugin_id)
    sv = latest_version(db, plugin_id)
    name = new_name or (src.name + " 副本")
    plugin = Plugin(id=new_id(), workspace_id=workspace_id, owner_user_id=user_id,
                    name=name, description=src.description, visibility="private",
                    status="draft", created_at=_now(), updated_at=_now())
    db.add(plugin)
    db.flush()
    pv = PluginVersion(id=new_id(), plugin_id=plugin.id, version_number=1, status="draft",
                       spec=dict(sv.spec) if sv else {}, created_by=user_id, created_at=_now())
    db.add(pv)
    db.flush()
    _add_bindings(db, pv, pv.spec)
    return {"plugin_id": plugin.id, "version_id": pv.id, "name": name}


def test_run(db: Session, *, workspace_id: str, user_id: str, plugin_id: str, input_text: str) -> dict:
    """Run the plugin's tools with its Skill/instructions as context, return a summary.

    中文补充：试运行会真实跑一次 agent（新建会话 + Run，挂载该插件版本），
    因此会产生工具调用、工件与模型计费；成功返回 reply/tool_calls/artifacts，
    并把结果写入 TestRun 作为留痕。
    异常约定：agent 执行中的任何异常都会被 rollback 并转成 {"status":"failed",...}，
    不向上抛——试运行失败是正常业务结果，不应打断管理台操作。
    参数：``input_text`` 为模拟用户输入；须先校验资源归属（router 层负责）。
    """
    from datetime import datetime as _dt
    from ...ids import new_id as _new_id
    from ...models import Conversation, Run as _Run, TestRun, Message, Artifact
    plugin = db.get(Plugin, plugin_id)
    if not plugin:
        raise NotFoundError("Plugin 不存在")
    pv = latest_version(db, plugin_id)
    if not pv:
        raise ValidationError("Plugin 没有版本")
    conv = Conversation(id=_new_id(), workspace_id=workspace_id, owner_user_id=user_id,
                        title="测试:" + (plugin.name or "插件"), created_at=_dt.utcnow(), updated_at=_dt.utcnow())
    db.add(conv); db.flush()
    run = _Run(id=_new_id(), workspace_id=workspace_id, conversation_id=conv.id,
              started_by_user_id=user_id, plugin_version_id=pv.id, status="created",
              created_at=_dt.utcnow(), context_snapshot={})
    db.add(run); db.flush(); db.commit()

    from app.business_worker import transport as business_transport
    from ...models import Workspace as _Ws, User as _User
    ws = db.get(_Ws, workspace_id)
    user = db.get(_User, user_id)
    events: list = []
    try:
        if business_transport.active():
            from app.business_worker.chat import prepare_existing_plugin_run
            prepared = prepare_existing_plugin_run(db, run, input_text)
            business_transport.execute_chat(prepared)
            # 原生宿主通过独立 Worker 操作更新台账，清除当前会话旧快照。
            db.expire_all()
            db.refresh(run)
        else:
            from ...modules.runtime.engine import run_chat
            for _ev in run_chat(db, db.get(_Run, run.id), ws, user,
                                 content=input_text, plugin_version_id=pv.id, events=events):
                pass
        db.commit()
    except Exception as exc:
        db.rollback()
        return {"status": "failed", "error": str(exc)[:500]}

    from sqlalchemy import select
    msgs = db.execute(select(Message).where(Message.conversation_id == conv.id)
                      .order_by(Message.sequence)).scalars().all()
    assist = [m for m in msgs if m.role == "assistant"]
    arts = db.execute(select(Artifact).where(Artifact.run_id == run.id)).scalars().all()
    # 原生执行失败时必须如实记录，不能因为生成器正常返回就判为通过。
    result_status = "passed" if run.status == "completed" else "failed"
    tr = TestRun(id=_new_id(), workspace_id=workspace_id, resource_type="plugin",
                 resource_version_id=pv.id, status=result_status,
                 input_json={"text": input_text},
                 output_json={"reply": (assist[-1].content if assist else "")},
                 created_by=user_id, created_at=_dt.utcnow())
    db.add(tr); db.commit()
    return {
        "status": result_status, "run_id": run.id,
        "reply": (assist[-1].content if assist else "")[:2000],
        "tool_calls": [t.tool_name for t in db.execute(select(ToolCall).where(ToolCall.run_id == run.id)).scalars().all()],
        "artifacts": [{"id": a.id, "name": a.name} for a in arts],
    }


def share(db: Session, plugin_id: str, visibility: str = "workspace") -> None:
    """修改可见范围（private / workspace）；其他取值抛 ValidationError，ID 不存在静默跳过。"""
    if visibility not in {"private", "workspace"}:
        raise ValidationError("Plugin 可见范围只支持 private 或 workspace")
    plugin = db.get(Plugin, plugin_id)
    if plugin:
        plugin.visibility = visibility
        plugin.updated_at = _now()
        db.commit()


def archive(db: Session, plugin_id: str) -> None:
    """软删除（status=archived）：版本与绑定保留，不再出现在列表与挂载入口。

    与 share 不同，这里对不存在的 ID 抛 NotFoundError（避免调用方以为已归档成功）。
    """
    plugin = db.get(Plugin, plugin_id)
    if not plugin:
        raise NotFoundError("Plugin 不存在")
    plugin.status = "archived"
    plugin.updated_at = _now()
    db.commit()
