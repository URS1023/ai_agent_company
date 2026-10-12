"""准备业务资源说明，执行循环和对话历史由 Open WebUI 管理。"""
from __future__ import annotations
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session
from ...models import KnowledgeBase, PluginVersion, Plugin, SkillVersion

def _attached_file_summaries(db: Session, workspace_id: str, file_ids: List[str]) -> List[str]:
    """把本次附加文件渲染成系统提示里的"id | 名称 | MIME"行，供模型调用 read_file 时取 id。

    只列元信息不读内容：内容由模型按需用 read_file 分页读取，避免把大文件塞进上下文。
    只读；文件不存在/已删除时只是不出现在列表里（由工具层报错）。
    """
    from ...modules.files.service import list_files
    if not file_ids:
        return []
    rows = list_files(db, workspace_id, file_ids)
    lines = []
    for f in rows:
        lines.append(f"{f.id} | {f.name} | {f.mime_type}")
    return lines

def _kb_descriptions(db: Session, workspace_id: str, kb_ids: Optional[List[str]]) -> List[str]:
    """列出本次可选知识库（"id | 名称"行），供模型决定 search_knowledge 检索哪个库。

    只返回本工作区 status=active 的库；`kb_ids` 为空表示"展示全部可用库"，
    非空时按 id 过滤——用户在会话里明确选择过的库优先于全量列表。
    只读，无副作用。
    """
    q = select(KnowledgeBase).where(KnowledgeBase.workspace_id == workspace_id,
                                    KnowledgeBase.status == "active")
    rows = db.execute(q).scalars().all()
    if kb_ids:
        idset = set(kb_ids)
        rows = [r for r in rows if r.id in idset]
    return [f"{kb.id} | {kb.name}" for kb in rows]

def _load_plugin_context(db: Session, workspace_id: str, plugin_version_id: Optional[str]) -> Optional[str]:
    """把插件版本及其引用的技能指令拼成一段提示文本（无插件时返回 None）。

    只接受属于当前工作区的插件（跨工作区引用直接忽略，防止越权读取他人插件定义）。
    返回：插件名 + 使用说明 + 各 skill 的 instructions；spec 缺字段时逐级跳过。
    只读，无副作用。
    """
    if not plugin_version_id:
        return None
    pv = db.get(PluginVersion, plugin_version_id)
    if not pv:
        return None
    plugin = db.get(Plugin, pv.plugin_id)
    if not plugin or plugin.workspace_id != workspace_id:
        return None
    parts = []
    spec = pv.spec or {}
    if plugin:
        parts.append(f"插件：{plugin.name}")
    if spec.get("instructions"):
        parts.append("使用说明：" + spec["instructions"])
    skill_ids = spec.get("skill_refs") or []
    for ref in skill_ids:
        sid = ref.get("skill_version_id") if isinstance(ref, dict) else ref
        if not sid:
            continue
        sv = db.get(SkillVersion, sid)
        if sv and sv.spec:
            s = sv.spec
            if s.get("instructions"):
                parts.append(f"方法[{s.get('name', 'skill')}]：" + s["instructions"])
    return "\n".join(parts)
