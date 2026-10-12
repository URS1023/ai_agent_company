"""Static contracts for business skill packages (offline, no DB/network).

中文说明：业务插件技能包（plugins/business/*）的静态契约测试：
- 每个包必须有合法 SKILL.md（name/description 非空、正文非空）且 references 可解析；
- 技能引用的工具必须由所属业务插件明确声明；
- 平台技能发现器能完整加载所有有效包（list_skills）。
"""
from pathlib import Path
import re

import pytest

from app.modules.octop_bridge.skills import BUSINESS_SKILLS_ROOT, list_skills


def _parse_frontmatter(text: str):
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    head = parts[1]
    metadata = {}
    for line in head.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            metadata[key.strip()] = value.strip().strip("'\"")
    return metadata, parts[2].strip()


def _packages():
    return sorted((entry for entry in BUSINESS_SKILLS_ROOT.iterdir() if entry.is_dir()),
                  key=lambda path: path.name.casefold())


def test_every_package_has_valid_manifest():
    assert BUSINESS_SKILLS_ROOT.is_dir()
    assert _packages(), "business_skills 目录不应为空"
    for package in _packages():
        manifest_path = package / "SKILL.md"
        assert manifest_path.is_file(), f"{package.name} 缺少 SKILL.md"
        metadata, content = _parse_frontmatter(manifest_path.read_text(encoding="utf-8"))
        assert metadata.get("name"), f"{package.name}: 缺少 name"
        assert metadata.get("description"), f"{package.name}: 缺少 description"
        assert content.strip(), f"{package.name}: 正文为空"
        assert metadata.get("disabled") is None and metadata.get("removed") is None, f"{package.name}: 被停用"


def test_referenced_resources_exist():
    for package in _packages():
        text = (package / "SKILL.md").read_text(encoding="utf-8")
        for reference in re.findall(r"references/[A-Za-z0-9_\-./]+\.md", text):
            assert (package / reference).is_file(), f"{package.name}: 引用的 {reference} 不存在"


def test_skill_tool_names_within_owning_plugin():
    """按插件实际能力校验，不能沿用旧聊天只读工具白名单误禁文件应用。"""
    from app.business_worker.registry import catalog
    declared = {skill["id"]: set(skill["tools"])
                for plugin in catalog()["plugins"] for skill in plugin.get("skills", [])}
    for package in _packages():
        text = (package / "SKILL.md").read_text(encoding="utf-8")
        mentioned = set(re.findall(r"`([A-Za-z][A-Za-z0-9_]{2,60})`", text))
        assert package.name in declared
        assert mentioned <= declared[package.name], f"{package.name} 引用了插件未声明的工具"


def test_legacy_resource_links_remain_readable():
    """历史已保存技能版本仍能按原标识读取资源。"""
    from app.modules.octop_bridge.skills import read_resource
    value = read_resource("presentation", "references/presentation-contracts.md")
    assert value["skill_name"] == "presentation" and value["content"]


def test_discovery_loads_all_packages():
    skills = list_skills()
    names = {item["skill_name"] for item in skills}
    expected = {package.name for package in _packages()}
    # 损坏/停用包会被跳过；有效包必须全部可发现（当前期望全部有效）。
    missing = sorted(expected - names)
    assert not missing, f"技能发现器未能加载: {missing}"
    for item in skills:
        assert item["description"] and item["content"], f"{item['skill_name']} 描述或正文为空"
