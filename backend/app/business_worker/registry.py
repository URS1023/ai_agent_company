"""业务应用清单是 HTTP 模块、工具组和 Skills 的唯一注册来源。"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def catalog():
    path = Path(os.environ.get("BUSINESS_APPS_MANIFEST") or ROOT / "configurations" / "business_apps.json")
    if not path.is_absolute():
        path = ROOT / path
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("schema_version") != 1 or not isinstance(value.get("plugins"), list):
        raise ValueError("业务插件清单版本或结构无效")
    all_plugins = value["plugins"]
    ids = [plugin.get("id") for plugin in all_plugins if isinstance(plugin, dict)]
    if len(ids) != len(all_plugins) or any(not isinstance(identity, str) or not identity for identity in ids) or len(set(ids)) != len(ids):
        raise ValueError("业务插件标识缺失或重复")
    enabled = [plugin for plugin in all_plugins if plugin.get("enabled", True) is not False]
    by_id = {plugin["id"]: plugin for plugin in enabled}
    visited, visiting = set(), set()
    def check(identity):
        if identity in visiting:
            raise ValueError("业务插件依赖形成循环：" + identity)
        if identity in visited:
            return
        visiting.add(identity)
        plugin = by_id[identity]
        for key in ("router_modules", "tools", "dependencies"):
            if not isinstance(plugin.get(key, []), list) or any(not isinstance(item, str) for item in plugin.get(key, [])):
                raise ValueError("业务插件字段必须是字符串数组：" + key)
        for dependency in plugin.get("dependencies", []):
            if dependency not in by_id:
                raise ValueError("已启用业务插件依赖缺失或已停用：" + identity + " -> " + dependency)
            check(dependency)
        visiting.remove(identity)
        visited.add(identity)
    for identity in by_id:
        check(identity)
    skill_ids = set()
    def dependency_tools(identity):
        plugin = by_id[identity]
        allowed = set(plugin.get("tools", []))
        for dependency in plugin.get("dependencies", []):
            allowed.update(dependency_tools(dependency))
        return allowed
    for plugin in enabled:
        allowed = dependency_tools(plugin["id"])
        for skill in plugin.get("skills", []):
            identity = skill.get("id") if isinstance(skill, dict) else None
            if not identity or identity in skill_ids:
                raise ValueError("业务技能标识缺失或重复")
            if not set(skill.get("tools", [])) <= allowed:
                raise ValueError("业务技能引用了插件依赖范围外的工具：" + identity)
            skill_ids.add(identity)
    return {**value, "plugins": enabled}


def router_modules():
    """保持清单顺序，跨业务应用共享路由只注册一次。"""
    return list(dict.fromkeys(name for plugin in catalog()["plugins"] for name in plugin.get("router_modules", [])))


def skills():
    return [skill for plugin in catalog()["plugins"] for skill in plugin.get("skills", [])]


def tool_names(skill_ids=None, business_module=None):
    """显式选技能时收窄工具；通用业务助手保留各业务插件声明的能力。"""
    plugins = catalog()["plugins"]
    selected = set(skill_ids or [])
    if selected:
        known = {skill["id"]: skill for skill in skills()}
        if selected - known.keys():
            from app.errors import ValidationError
            raise ValidationError("选择的业务技能不存在")
        names = {name for identity in selected for name in known[identity].get("tools", [])}
        names.update({"current_datetime", "list_files", "read_file", "list_business_skills", "read_business_skill_resource"})
        return names
    return {name for plugin in plugins for name in plugin.get("tools", [])}


def skill_instructions(skill_ids):
    """读取已登记的本地中文技能资源，不读取用户任意文件路径。"""
    known = {skill["id"]: skill for skill in skills()}
    texts = []
    for identity in skill_ids or []:
        skill = known.get(identity)
        if not skill:
            from app.errors import ValidationError
            raise ValidationError("业务技能不存在")
        path = (ROOT / skill["path"]).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError("技能资源必须在项目内")
        texts.append(path.read_text(encoding="utf-8-sig"))
    return "\n\n".join(texts)


def capabilities():
    return {"engine": "dsh", "protocol_version": 2,
            "modes": [{"id": "standard", "available": True},
                      {"id": "web", "available": False, "reason": "尚未配置联网检索业务插件"},
                      {"id": "deep", "available": False, "reason": "尚未配置深度研究业务插件"}],
            "features": {"queue": True, "steer": False, "reasoning": True,
                         "questions": False, "tasks": True, "deliveries": True},
            "skills": skills(), "plugins": catalog()["plugins"]}
