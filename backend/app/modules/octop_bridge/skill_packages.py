"""移植自 Octop 的技能包校验，补充 Windows 路径边界和只读 ZIP 解析。"""
from __future__ import annotations

import io
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

MAX_SKILL_FILES = 2_000
MAX_SKILL_BYTES = 64 * 1024 * 1024
_WINDOWS_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class SkillPackageError(ValueError):
    """技能包内容或路径不符合约定。"""


class SkillPackageTooLarge(SkillPackageError):
    """技能包超过文件数量或大小上限。"""


@dataclass(frozen=True)
class ResolvedSkillPackage:
    """保存来源与相对文件路径，不执行包内脚本。"""

    slug: str
    files: tuple[tuple[str, bytes], ...]
    source: str
    source_url: str = ""

    def workspace_uploads(self) -> list[tuple[str, bytes]]:
        """转换为工作区内的技能文件清单。"""
        return [(f"skills/{self.slug}/{path}", data) for path, data in self.files]


def _validate_segment(segment: str) -> None:
    """拒绝会被 Windows 重新解释或映射到设备的路径分量。"""
    if (not segment or segment in {".", ".."} or segment.endswith((" ", "."))
            or any(ord(char) < 32 or char in ':<>"|?*' for char in segment)
            or _WINDOWS_RESERVED.match(segment)):
        raise SkillPackageError("技能包路径含不安全的文件名")


def validate_skill_slug(slug: str) -> str:
    """技能名称只能表示一个普通目录，禁止隐藏目录和路径拼接。"""
    if not isinstance(slug, str) or slug != slug.strip() or slug.startswith("."):
        raise SkillPackageError("技能名称无效")
    if "/" in slug or "\\" in slug:
        raise SkillPackageError("技能名称不能包含目录分隔符")
    _validate_segment(slug)
    return slug


def normalize_relative_path(value: str) -> str:
    """使用相对 POSIX 路径，同时禁止驱动器、ADS 和目录穿越。"""
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise SkillPackageError("技能包必须使用普通相对路径")
    directory = value.endswith("/")
    parts = value[:-1].split("/") if directory else value.split("/")
    for part in parts:
        _validate_segment(part)
    path = PurePosixPath(*parts).as_posix()
    return f"{path}/" if directory else path


def normalize_skill_files(
    files: list[tuple[str, bytes]], *, require_manifest: bool = True,
) -> tuple[tuple[str, bytes], ...]:
    """验证文件数量、总大小、大小写冲突与根清单编码。"""
    if len(files) > MAX_SKILL_FILES:
        raise SkillPackageTooLarge("技能包文件数超过 2000")
    normalized: list[tuple[str, bytes]] = []
    seen: dict[str, bool] = {}
    total = 0
    for raw_path, content in files:
        path = normalize_relative_path(raw_path)
        key = path.rstrip("/").casefold()
        if key in seen:
            raise SkillPackageError("技能包含重复路径或大小写冲突")
        if not isinstance(content, bytes):
            raise SkillPackageError("技能包文件内容必须为字节数据")
        if path.endswith("/") and content:
            raise SkillPackageError("目录记录不能包含文件数据")
        seen[key] = path.endswith("/")
        total += len(content)
        if total > MAX_SKILL_BYTES:
            raise SkillPackageTooLarge("技能包总大小超过 64 MB")
        normalized.append((path, content))
    # 防止先写普通文件，再将同一位置当作其他文件的父目录。
    for key in seen:
        parts = key.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in seen and not seen[parent]:
                raise SkillPackageError("技能包存在文件与目录冲突")
    manifest = next((data for path, data in normalized if path == "SKILL.md"), None)
    if require_manifest and manifest is None:
        raise SkillPackageError("技能包缺少根目录 SKILL.md")
    if manifest is not None:
        try:
            manifest.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillPackageError("SKILL.md 必须为 UTF-8 文本") from exc
    return tuple(normalized)


def resolve_skill_package(*, slug: str, files: list[tuple[str, bytes]],
                          source: str, source_url: str = "") -> ResolvedSkillPackage:
    """统一封装目录、ZIP 等来源的已验证技能包。"""
    return ResolvedSkillPackage(validate_skill_slug(slug), normalize_skill_files(files),
                                source.strip() or "unknown", source_url.strip())


def is_link_or_reparse(entry: os.stat_result) -> bool:
    """符号链接和 Windows junction 都不能扩大技能可读取的目录范围。"""
    return bool(stat.S_ISLNK(entry.st_mode) or (
        getattr(entry, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))


def read_skill_directory(skill_dir: Path) -> list[tuple[str, bytes]]:
    """只读普通目录和文件，拒绝链接、特殊文件及超量技能包。"""
    try:
        root_stat = skill_dir.lstat()
    except OSError as exc:
        raise SkillPackageError("技能目录无法读取") from exc
    if is_link_or_reparse(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
        raise SkillPackageError("技能根目录必须是普通目录")
    files: list[tuple[str, bytes]] = []
    total = 0

    def reject_walk_error(error: OSError) -> None:
        """目录读取失败时拒绝不完整的技能包。"""
        raise SkillPackageError("技能包存在无法读取的目录") from error

    for dirpath, dirnames, filenames in os.walk(skill_dir, followlinks=False, onerror=reject_walk_error):
        current = Path(dirpath)
        for name in dirnames:
            entry = (current / name).lstat()
            if is_link_or_reparse(entry) or not stat.S_ISDIR(entry.st_mode):
                raise SkillPackageError("技能包不允许符号链接或 junction")
            normalize_relative_path((current / name).relative_to(skill_dir).as_posix() + "/")
        for name in filenames:
            path = current / name
            entry = path.lstat()
            if is_link_or_reparse(entry) or not stat.S_ISREG(entry.st_mode):
                raise SkillPackageError("技能包不允许链接或特殊文件")
            total += entry.st_size
            if total > MAX_SKILL_BYTES or len(files) >= MAX_SKILL_FILES:
                raise SkillPackageTooLarge("技能包超过大小或文件数量上限")
            files.append((path.relative_to(skill_dir).as_posix(), path.read_bytes()))
    return list(normalize_skill_files(files))


def parse_skill_zip(payload: bytes) -> tuple[tuple[str, bytes], ...]:
    """在内存解析 ZIP，不解压到宿主文件系统，也不执行文件。"""
    if len(payload) > MAX_SKILL_BYTES:
        raise SkillPackageTooLarge("压缩技能包超过 64 MB")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_SKILL_FILES:
                raise SkillPackageTooLarge("技能包文件数超过 2000")
            if sum(entry.file_size for entry in entries) > MAX_SKILL_BYTES:
                raise SkillPackageTooLarge("技能包解压大小超过 64 MB")
            files = []
            for entry in entries:
                path = normalize_relative_path(entry.filename)
                mode = entry.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (kind not in {0, stat.S_IFREG, stat.S_IFDIR} or (entry.flag_bits & 1)
                        or (entry.external_attr & 0x400)):
                    raise SkillPackageError("技能包 ZIP 不允许链接、特殊文件或加密项")
                if (kind == stat.S_IFDIR and not entry.is_dir()) or (kind == stat.S_IFREG and entry.is_dir()):
                    raise SkillPackageError("技能包 ZIP 目录标记不一致")
                with archive.open(entry) as stream:
                    data = stream.read(MAX_SKILL_BYTES + 1)
                if len(data) > MAX_SKILL_BYTES:
                    raise SkillPackageTooLarge("技能包单文件超过 64 MB")
                files.append((path, data))
            return normalize_skill_files(files)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise SkillPackageError("技能包不是可读取的普通 ZIP 文件") from exc


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """读取技能 YAML 头；格式错误时明确报错，避免默默加载错误规范。"""
    text = text.lstrip("\ufeff")
    prefix = re.match(r"\A(?:\s|<!--[\s\S]*?-->)*", text)
    start = prefix.end() if prefix else 0
    match = re.match(r"---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)", text[start:])
    if not match:
        if text[start:].startswith("---"):
            raise SkillPackageError("技能 YAML 头缺少闭合标记")
        return {}, text
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillPackageError("技能 YAML 头格式错误") from exc
    if not isinstance(metadata, dict):
        raise SkillPackageError("技能 YAML 头必须为对象")
    return metadata, text[start + match.end():].lstrip("\r\n")
