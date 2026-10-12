"""发现仓库维护的业务技能，并按需只读访问技能包中的文本资料。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .skill_packages import (
    SkillPackageError, is_link_or_reparse, normalize_relative_path,
    parse_frontmatter, read_skill_directory, validate_skill_slug,
)

logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
BUSINESS_SKILLS_ROOT = _PROJECT_ROOT / "plugins" / "business"
LEGACY_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "business_skills"
MAX_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_RESOURCE_CHARS = 48_000
_TEXT_SUFFIXES = frozenset({".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".csv",
                            ".sql", ".py", ".js", ".ts", ".html", ".css", ".xml", ".toml", ".ini"})


def _trusted_root(root: Path | None) -> Path:
    """目录只能由服务端配置提供，不接收模型指定的宿主根路径。"""
    selected = Path(root) if root is not None else BUSINESS_SKILLS_ROOT
    # 检查整个路径链，避免父目录 junction 将包映射到可信目录以外。
    for part in [selected, *selected.parents]:
        if part.exists() and is_link_or_reparse(part.lstat()):
            raise SkillPackageError("技能仓库路径不能包含链接或 junction")
    return selected.resolve()


def list_skills(*, root: Path | None = None) -> list[dict[str, Any]]:
    """列出有效技能的说明和资源目录，损坏技能不进入模型能力清单。"""
    directory = _trusted_root(root)
    if not directory.exists():
        return []
    result = []
    declared = None
    if root is None:
        # 新平台以业务插件清单为注册来源，目录中未注册的草稿不会自动启用。
        from app.business_worker.registry import skills
        declared = {item["id"] for item in skills()}
    for entry in sorted(directory.iterdir(), key=lambda path: path.name.casefold()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if declared is not None and entry.name not in declared:
            continue
        try:
            skill_name = validate_skill_slug(entry.name)
            files = read_skill_directory(entry)
            manifest = next(data for path, data in files if path == "SKILL.md").decode("utf-8")
            metadata, content = parse_frontmatter(manifest)
            if metadata.get("disabled") or metadata.get("removed"):
                continue
            description = str(metadata.get("description") or "").strip()
            if not description or not content.strip():
                raise SkillPackageError("业务技能必须有描述和正文")
            result.append({"skill_name": skill_name, "name": str(metadata.get("name") or skill_name),
                           "description": description, "content": content,
                           "resources": [{"path": path, "size": len(data)} for path, data in files
                                         if path != "SKILL.md" and not path.endswith("/")]})
        except (SkillPackageError, OSError, UnicodeError) as exc:
            logger.warning("业务技能加载失败 skill=%s reason=%s", entry.name, exc)
    return result


def read_resource(skill_name: str, path: str, *, root: Path | None = None,
                  max_chars: int = 24_000) -> dict[str, Any]:
    """只读某个有效技能包中的 UTF-8 文本，长内容显式标记截断。"""
    name = validate_skill_slug(skill_name)
    relative = normalize_relative_path(path)
    if relative.endswith("/") or Path(relative).suffix.lower() not in _TEXT_SUFFIXES:
        raise SkillPackageError("技能资源必须是受支持的普通文本文件")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 0 < max_chars <= MAX_RESOURCE_CHARS:
        raise SkillPackageError("技能资源读取长度必须在 1 到 48000 字符之间")
    directory = _trusted_root(root)
    package = directory / name
    if root is None and not package.exists():
        # 已保存的技能版本可能引用旧资源名，只读兼容这些链接，不重新加入发现目录。
        legacy = _trusted_root(LEGACY_SKILLS_ROOT)
        if (legacy / name).is_dir():
            package = legacy / name
    # 校验整包，避免单独读取一个没有有效清单或含链接的伪技能目录。
    files = dict(read_skill_directory(package))
    manifest = files["SKILL.md"].decode("utf-8")
    metadata, content = parse_frontmatter(manifest)
    if metadata.get("disabled") or metadata.get("removed"):
        raise SkillPackageError("技能已停用")
    if not metadata.get("description") or not content.strip():
        raise SkillPackageError("技能清单不完整")
    target = package / relative
    if not target.resolve().is_relative_to(package.resolve()):
        raise SkillPackageError("技能资源越出当前技能目录")
    data = files.get(relative)
    if data is None:
        raise SkillPackageError("技能资源不存在")
    if len(data) > MAX_RESOURCE_BYTES:
        raise SkillPackageError("单次可读取的技能文本不能超过 2 MB")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillPackageError("技能资源不是 UTF-8 文本") from exc
    if "\x00" in text:
        raise SkillPackageError("技能资源包含二进制数据")
    return {"skill_name": name, "path": relative, "content": text[:max_chars],
            "truncated": len(text) > max_chars, "total_chars": len(text)}
