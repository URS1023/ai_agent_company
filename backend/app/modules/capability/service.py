# -*- coding: utf-8 -*-
"""Capability Builder — natural-language create/modify of Skill / Connector / Plugin,
and Run Summarizer (chat -> Skill / Plugin drafts, §17/§18/§46).

中文补充（能力构建器）：
- 职责：把"一次对话/一次运行"沉淀为可复用的能力对象（Skill 技能、Plugin 插件包），
  并提供自然语言修改能力定义、意图分类与工具参数编译三个入口。
- 权限与治理：模型生成的只是**草稿**（draft），必须经人工确认/发布流程才会生效；
  生成 Plugin 时工具引用会用 ToolGateway.list_available 做白名单过滤（模型提到的
  不存在的工具名会被丢弃），避免生成引用不到或越权的配置。
- 外部依赖：模型服务（chat_for_run，profile=reasoning，温度取低值以保证可复现）、
  skill/plugin 服务（落库与版本管理）；本模块自身不写表，全部委托给子服务。
- 修改入口注意：``modify_authorized`` 才是带资源级鉴权的对外入口，
  直接调用 ``modify`` 不做目标资源的编辑权限校验（仅供内部已授权路径使用）。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import select

from ...errors import NotFoundError, ValidationError
from ...common.authorization import require_resource
from ...providers.model import chat_for_run
from ...providers.tool.gateway import ToolGateway
from ...models import Message, Run, RunStep, ToolCall
from ..skill import service as skill_service
from ..plugin import service as plugin_service
from .planner import classify_intent, extract_json

logger = logging.getLogger("capability")


# ------------------------------------------------------------------
# 1. Intent classification (§17)
# ------------------------------------------------------------------
def analyze(db, workspace_id: str, text: str) -> dict:
    """判断用户这句话想创建/修改哪类能力对象（skill/connector/plugin/...）。

    返回 {"object","reason"}；分类失败时 planner 会回落到 other 而不是报错，
    让上层可以按"普通对话"继续处理。会调用一次模型，因此有计费与延迟成本。
    """
    result = classify_intent(text, workspace_id=workspace_id)
    return result


# ------------------------------------------------------------------
# 2. Run Summarizer (§18)
# ------------------------------------------------------------------
def summarize_run(db, run_id: str, workspace_id: str | None = None) -> dict:
    """Build a compact execution summary of a run for capability generation.

    中文补充：取该运行所属会话的前 12 条消息（每条截断 600 字）、全部工具调用
    （参数截断 300 字）与步骤名，组成给模型的"执行记录"。
    截断是刻意的：沉淀能力只需要方法与顺序，不需要原样回放全部数据，
    同时避免把大量业务数据送进模型上下文。
    异常：Run 不存在或 workspace_id 不匹配抛 NotFoundError（不做跨工作区读取）。
    """
    run = db.get(Run, run_id)
    if not run or (workspace_id is not None and run.workspace_id != workspace_id):
        raise NotFoundError("Run 不存在")
    messages = db.execute(select(Message).where(Message.conversation_id == run.conversation_id)
                          .order_by(Message.sequence)).scalars().all()
    steps = db.execute(select(RunStep).where(RunStep.run_id == run_id)
                       .order_by(RunStep.sequence)).scalars().all()
    tool_calls = db.execute(select(ToolCall).where(ToolCall.run_id == run_id)
                            .order_by(ToolCall.started_at)).scalars().all()
    conv = [f"{m.role}: {m.content[:600]}" for m in messages[:12] if m.content]
    tools = [f"{t.tool_name}({json.dumps(t.arguments_json, ensure_ascii=False)[:300]}) -> {t.status}"
             for t in tool_calls]
    ctx = run.context_snapshot or {}
    return {
        "run_id": run.id, "conversation": "\n".join(conv),
        "tool_calls": tools,
        "kb_ids": ctx.get("knowledge_base_ids") or [],
        "file_ids": ctx.get("file_ids") or [],
        "steps": [s.name for s in steps],
    }


_SKILL_GEN_SYSTEM = """你是企业 AI 能力沉淀助手。根据一次任务的执行记录，把“完成这类任务的方法”抽象成 Skill。
只输出 JSON：
{
  "name": "技能名（中文简短，如：经营分析报告编制）",
  "description": "技能用途一句话",
  "instructions": "给 LLM 的完整操作指引，含步骤、规则、注意事项（中文，300字以内）",
  "rules": ["...", "..."],
  "input_description": "输入说明",
  "output_description": "输出说明",
  "recommended_tools": ["search_knowledge", "python_execute", ...],
  "required_resources": ["知识库(公司资质)", ...]
}
"""

_PLUGIN_GEN_SYSTEM = """你是企业 AI 能力沉淀助手。根据一次任务的执行记录，把“业务能力包”抽象成 Plugin。
Plugin = 方法(Skill) + 工具(Tools) + 资源绑定(Resource Bindings)。
只输出 JSON：
{
  "name": "插件名（中文简短）",
  "description": "用途一句话",
  "instructions": "使用说明",
  "tool_refs": ["工具名数组，如 search_knowledge, python_execute, render_document, render_presentation"],
  "skill_refs": [],
  "resource_bindings": [{"resource_type": "knowledge|datasource|file", "resource_id": "内置时可为空字符串", "config": {}}],
  "input_schema": {"type": "object", "properties": {}},
  "output_schema": {"type": "object", "properties": {}}
}
"""


def generate_from_run(db, *, workspace_id: str, run_id: str, want: str,
                          owner_user_id: str = "") -> dict:
    """按执行记录生成 Skill（技能）或 Plugin（插件）草稿。

    参数：``want`` 只能是 skill/plugin；``owner_user_id`` 决定草稿归属。
    流程：汇总运行 → 模型产出 JSON → 规范化字段（缺失字段用空值兜底）→ 交给
    skill/plugin 服务落库为草稿。
    Plugin 的工具引用会用网关的可用工具清单过滤（``tool_refs`` 只保留当前工作区
    真实可见的工具），从源头避免写出指向不存在/已停用工具的插件。
    返回：{"kind","spec","created"}；异常：want 非法或运行不可读时抛业务错误。
    副作用：写 skill/plugin 表并 commit（状态为 draft）。
    """
    if want not in {"skill", "plugin"}:
        raise ValidationError("只能从运行记录生成 Skill 或 Plugin")
    summary = summarize_run(db, run_id, workspace_id)
    system = _SKILL_GEN_SYSTEM if want == "skill" else _PLUGIN_GEN_SYSTEM
    user = "任务执行记录：\n会话内容：\n" + summary["conversation"] + \
           "\n工具调用序列：\n" + "\n".join(summary["tool_calls"]) + \
           "\n请据此生成" + ("Skill JSON。" if want == "skill" else "Plugin JSON。")
    resp = chat_for_run("reasoning", [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0.1, workspace_id=workspace_id)
    plan = extract_json(resp.get("content") or "")
    if want == "skill":
        spec = {
            "name": plan.get("name") or "新技能",
            "description": plan.get("description") or "",
            "instructions": plan.get("instructions") or "",
            "rules": plan.get("rules") or [],
            "input_description": plan.get("input_description") or "",
            "output_description": plan.get("output_description") or "",
            "examples": [],
            "recommended_tools": plan.get("recommended_tools") or [],
            "required_resources": plan.get("required_resources") or [],
        }
        result = skill_service.create_skill(db, workspace_id=workspace_id, owner_user_id=owner_user_id,
                                            name=spec["name"], description=spec["description"],
                                            spec=spec)
        db.commit()
        return {"kind": "skill", "spec": spec, "created": result}
    # plugin
    gateway = ToolGateway(db)
    available = {s.name for s in gateway.list_available(workspace_id, include_builtin=True)}
    tool_refs = [t for t in (plan.get("tool_refs") or []) if t in available]
    spec = {
        "name": plan.get("name") or "新插件",
        "description": plan.get("description") or "",
        "instructions": plan.get("instructions") or "",
        "tool_refs": tool_refs,
        "skill_refs": (plan.get("skill_refs") or []),
        "resource_bindings": (plan.get("resource_bindings") or []),
        "input_schema": plan.get("input_schema") or {"type": "object", "properties": {}},
        "output_schema": plan.get("output_schema") or {"type": "object", "properties": {}},
    }
    result = plugin_service.create_plugin(db, workspace_id=workspace_id, owner_user_id=owner_user_id,
                                          name=spec["name"], description=spec["description"],
                                          spec=spec)
    db.commit()
    return {"kind": "plugin", "spec": spec, "created": result}


# ------------------------------------------------------------------
# 2b. Parameter Compiler (§13): intent -> validated tool args
# ------------------------------------------------------------------
def compile_tool_arguments(db, workspace_id: str, tool_name: str, intent: str,
                           references: dict = None) -> dict:
    """把自然语言意图编译为某工具的、已通过 Schema 校验的参数。

    工具解析走 ToolGateway.resolve（受治理停用与工作区范围约束），
    references 只作为 x-lookup 的候选映射传入，不做数据库查询。
    返回 {"tool","arguments","schema"}；工具不存在抛 NotFoundError，
    编译/校验失败抛 ValidationError 或 SchemaValidationError。不写库。
    """
    from ...providers.tool.gateway import ToolGateway
    from ...providers.tool.compiler import compile_arguments, reference_resolver
    from ...errors import NotFoundError
    gateway = ToolGateway(db)
    spec = gateway.resolve(workspace_id, tool_name)
    if spec is None:
        raise NotFoundError(f"未找到工具 {tool_name}")
    args = compile_arguments(spec, intent, context={"references": references or {}},
                             resolver=reference_resolver(references or {}), workspace_id=workspace_id)
    return {"tool": spec.name, "arguments": args, "schema": spec.input_schema}


# ------------------------------------------------------------------
# 3. Natural-language modification (§46)
# ------------------------------------------------------------------
def modify(db, *, workspace_id: str, resource_type: str, resource_id: str, instruction: str,
                 user_id: str = "") -> dict:
    """按自然语言修改能力定义，产出**新的草稿版本**（不覆盖已发布版本）。

    参数：``resource_type`` 支持 skill/plugin；``instruction`` 是用户的修改要求；
    skill 分支由模型输出完整新 spec，plugin 分支额外传 workspace_id 让
    plugin 服务重新解析资源绑定。
    返回：{"kind","version_id","version_number","status":"draft","spec"}。
    异常：资源不存在抛 NotFoundError，类型不支持或无版本抛 ValidationError。
    权限提示：本函数不做资源级鉴权，对外必须经 ``modify_authorized`` 调用。
    副作用：写新版本记录（草稿）并调用模型。
    """
    if resource_type == "skill":
        skill = skill_service.get_skill(db, workspace_id, resource_id)
        lv = skill_service.latest_version(db, resource_id)
        if not lv:
            raise ValidationError("Skill 没有版本")
        system = "你是企业能力修改助手。根据用户的修改要求，更新给定的 Skill 定义，只输出 JSON（结构同输入）。不要改动未涉及的部分。"
        user = "现有 Skill：\n" + json.dumps(lv.spec, ensure_ascii=False) + \
               "\n修改要求：" + instruction
        resp = chat_for_run("reasoning", [{"role": "system", "content": system},
                                          {"role": "user", "content": user}], temperature=0.1,
                            workspace_id=workspace_id)
        new_spec = extract_json(resp.get("content") or "")
        nv = skill_service.new_draft_version(db, resource_id, user_id, new_spec)
        return {"kind": "skill", "version_id": nv.id, "version_number": nv.version_number,
                "status": "draft", "spec": new_spec}
    if resource_type == "plugin":
        plugin = plugin_service.get_plugin(db, workspace_id, resource_id)
        lv = plugin_service.latest_version(db, resource_id)
        if not lv:
            raise ValidationError("Plugin 没有版本")
        system = "你是企业能力修改助手。根据用户的修改要求，更新给定的 Plugin 定义，只输出 JSON（结构同输入）。"
        user = "现有 Plugin：\n" + json.dumps(lv.spec, ensure_ascii=False) + \
               "\n修改要求：" + instruction
        resp = chat_for_run("reasoning", [{"role": "system", "content": system},
                                          {"role": "user", "content": user}], temperature=0.1,
                            workspace_id=workspace_id)
        new_spec = extract_json(resp.get("content") or "")
        # keep bindings resolved by the plugin service
        nv = plugin_service.new_draft_version(db, resource_id, user_id, new_spec, workspace_id)
        return {"kind": "plugin", "version_id": nv.id, "version_number": nv.version_number,
                "status": "draft", "spec": nv.spec}
    raise ValidationError(f"暂不支持修改 {resource_type} 类型")


def modify_authorized(ctx, *, resource_type: str, resource_id: str, instruction: str) -> dict:
    """Modify a capability only after checking the target resource's edit scope.

    The legacy endpoint is globally guarded as ``capabilities.create``.  That
    action permits entering the capability builder, but it does not grant edit
    rights over every Skill or Plugin in the workspace.  Resolve the target
    through the resource-specific policy before invoking the legacy builder.

    中文补充：对外唯一允许的修改入口。先按类型取到目标资源（get_skill/get_plugin
    会做工作区与存在性校验），再用 require_resource 校验 skills.edit / plugins.edit
    的**资源级**权限——仅持有 capabilities.create（能进入能力构建器）不等于有权改
    别人的技能/插件。校验通过后才把 ctx.db 交给内部 modify。
    异常：类型不支持抛 ValidationError；权限不足由 require_resource 抛 AppError。
    """
    if resource_type == "skill":
        resource = skill_service.get_skill(ctx.db, ctx.workspace_id, resource_id)
        require_resource(ctx, "skills.edit", resource)
    elif resource_type == "plugin":
        resource = plugin_service.get_plugin(ctx.db, ctx.workspace_id, resource_id)
        require_resource(ctx, "plugins.edit", resource)
    else:
        raise ValidationError(f"暂不支持修改 {resource_type} 类型")
    return modify(ctx.db, workspace_id=ctx.workspace_id, resource_type=resource_type,
                  resource_id=resource_id, instruction=instruction, user_id=ctx.user_id)
