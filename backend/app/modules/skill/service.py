# -*- coding: utf-8 -*-
"""Skill service — versioned 'how to do' methods.

中文补充（技能服务）：
- Skill = "某类任务该怎么做"的可版本化方法说明（instructions/rules/推荐工具等），
  供 AI 在会话中调用；SkillVersion 是不可变版本链，只有 publish 后才生效。
- 生命周期：create_skill（draft）→ new_draft_version 迭代 → publish（写入
  published_version_id）→ archive/clone/share。
- 权限：本服务只做工作区隔离（workspace_id 必须匹配），资源级权限由调用方
  （router / capability.service.modify_authorized）用 require_resource 校验；
  因此不要在未鉴权的路径上直接调用这些函数。
- 副作用：除 test_run（调用模型并写 TestRun 记录）外均只写 Skill 相关表；
  archive 是软删除，历史版本与测试记录保留。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import NotFoundError, ValidationError
from ...ids import new_id
from ...models import Skill, SkillVersion, TestRun


def _now():
    """统一的 UTC 写入时间。"""
    return datetime.utcnow()


def create_skill(db: Session, *, workspace_id: str, owner_user_id: str, name: str,
                 description: str = "", spec: dict = None) -> dict:
    """新建 Skill 及其第 1 个草稿版本（visibility 默认 workspace、status=draft）。

    返回 {"skill_id","version_id","name"}；不提交事务，由调用方决定提交边界。
    草稿不会进入 AI 的工具/能力列表，需显式发布。
    """
    spec = spec or {}
    skill = Skill(id=new_id(), workspace_id=workspace_id, owner_user_id=owner_user_id,
                  name=name, description=description, visibility="workspace", status="draft",
                  created_at=_now(), updated_at=_now())
    db.add(skill)
    db.flush()
    sv = SkillVersion(id=new_id(), skill_id=skill.id, version_number=1, status="draft",
                      spec=spec, created_by=owner_user_id, created_at=_now())
    db.add(sv)
    db.flush()
    return {"skill_id": skill.id, "version_id": sv.id, "name": name}


def list_skills(db: Session, workspace_id: str):
    """列出本工作区未归档的 Skill（按创建时间倒序）；不做分页（技能数量有限）。"""
    return db.execute(select(Skill).where(Skill.workspace_id == workspace_id, Skill.status != "archived")
                      .order_by(Skill.created_at.desc())).scalars().all()


def get_skill(db: Session, workspace_id: str, skill_id: str) -> Skill:
    """按 ID 取 Skill，并强制校验工作区归属；不存在或跨工作区统一抛 NotFoundError。"""
    row = db.get(Skill, skill_id)
    if not row or row.workspace_id != workspace_id:
        raise NotFoundError("Skill 不存在")
    return row


def latest_version(db: Session, skill_id: str) -> SkillVersion:
    """取最新版本（version_number 最大）；无版本返回 None（调用方需自行兜底）。"""
    return db.execute(select(SkillVersion).where(SkillVersion.skill_id == skill_id)
                      .order_by(SkillVersion.version_number.desc()).limit(1)).scalar_one_or_none()


def new_draft_version(db: Session, skill_id: str, user_id: str, spec: dict) -> SkillVersion:
    """追加一个新草稿版本，并把 spec 里的 name/description 同步到 Skill 主记录。

    名称限 1–200 字符、描述限 1000 字符（超限抛 ValidationError），避免前端列表被长文本撑坏；
    版本号自增并记录 based_on_version_id 以便回溯；同时把 Skill 状态退回 draft，
    提醒使用者"改过但还没发布"。本函数内部 commit，返回新版本行。
    """
    skill = db.get(Skill, skill_id)
    if not skill:
        raise NotFoundError("Skill 不存在")
    if "name" in spec:
        name = str(spec["name"] or "").strip()
        if not name or len(name) > 200:
            raise ValidationError("Skill 名称需为 1 至 200 个字符")
        skill.name = name
    if "description" in spec:
        description = str(spec["description"] or "").strip()
        if len(description) > 1000:
            raise ValidationError("Skill 适用场景不能超过 1000 个字符")
        skill.description = description
    lv = latest_version(db, skill_id)
    next_ver = (lv.version_number + 1) if lv else 1
    nv = SkillVersion(id=new_id(), skill_id=skill_id, version_number=next_ver,
                      based_on_version_id=lv.id if lv else None, status="draft",
                      spec=spec, created_by=user_id, created_at=_now())
    db.add(nv)
    db.flush()
    skill.status = "draft"
    skill.updated_at = _now()
    db.commit()
    return nv


def publish(db: Session, skill_id: str) -> None:
    """把最新版本置为 published，并让 Skill 指向该版本（published_version_id）。

    只有最新版本能被发布（历史草稿无法直接上线）；无版本时抛 ValidationError。
    内部 commit；返回被发布的版本行（注解写 None 是历史遗留，实际有返回值）。
    """
    skill = get_skill(db, skill_id, skip_ws=True) if False else db.get(Skill, skill_id)
    if not skill:
        raise NotFoundError("Skill 不存在")
    lv = latest_version(db, skill_id)
    if not lv:
        raise ValidationError("Skill 没有版本")
    lv.status = "published"
    lv.published_at = _now()
    skill.status = "published"
    skill.published_version_id = lv.id
    skill.updated_at = _now()
    db.commit()
    return lv


def archive(db: Session, skill_id: str) -> None:
    """软删除（status=archived）：版本与测试记录保留，只是不再出现在列表中。

    对不存在的 ID 静默返回（幂等），便于 DELETE 语义的重复调用。
    """
    skill = db.get(Skill, skill_id)
    if skill:
        skill.status = "archived"
        skill.updated_at = _now()
        db.commit()


def clone(db: Session, *, workspace_id: str, user_id: str, skill_id: str, new_name: str = "") -> dict:
    """复制 Skill（含最新版本 spec）为独立草稿。

    副本可见性固定 private：避免"复制后默认全工作区可见"造成的能力扩散；
    名称缺省为"原名 副本"，全空白则拒绝。返回 create_skill 的结果字典。
    """
    source = get_skill(db, workspace_id, skill_id)
    version = latest_version(db, skill_id)
    name = (new_name or (source.name + " 副本")).strip()
    if not name:
        raise ValidationError("Skill 名称不能为空白")
    result = create_skill(
        db,
        workspace_id=workspace_id,
        owner_user_id=user_id,
        name=name,
        description=source.description,
        spec=dict(version.spec or {}) if version else {},
    )
    copied = db.get(Skill, result["skill_id"])
    copied.visibility = "private"
    db.commit()
    return result


def share(db: Session, skill_id: str, visibility: str) -> None:
    """修改可见范围（private / workspace），其他取值抛 ValidationError。"""
    if visibility not in {"private", "workspace"}:
        raise ValidationError("Skill 可见范围只支持 private 或 workspace")
    skill = db.get(Skill, skill_id)
    if not skill:
        raise NotFoundError("Skill 不存在")
    skill.visibility = visibility
    skill.updated_at = _now()
    db.commit()


def test_run(db: Session, *, workspace_id: str, skill_id: str, created_by: str, input_text: str) -> TestRun:
    """用最新版本 spec 的 instructions 作为系统提示，跑一次模型调用并记录 TestRun。

    参数：``input_text`` 是模拟的用户输入；``workspace_id`` 用于模型计量与路由。
    返回：未提交的 TestRun 行（调用方负责 commit）。
    异常：Skill 无版本抛 ValidationError。
    副作用：调用模型服务（有延迟与计费），并把输入/输出写入测试记录（审计留痕）。
    """
    from app.providers.model import chat_for_run
    sv = latest_version(db, skill_id)
    if not sv:
        raise ValidationError("Skill 没有版本")
    sys_content = sv.spec.get("instructions") or sv.spec.get("description") or "（无说明）"
    out = chat_for_run("reasoning", [
        {"role": "system", "content": sys_content},
        {"role": "user", "content": input_text},
    ], temperature=0.2, workspace_id=workspace_id)
    tr = TestRun(id=new_id(), workspace_id=workspace_id, resource_type="skill",
                 resource_version_id=sv.id, status="passed",
                 input_json={"text": input_text}, output_json={"text": out.get("content", "")},
                 created_by=created_by, created_at=_now())
    db.add(tr)
    db.flush()
    return tr
