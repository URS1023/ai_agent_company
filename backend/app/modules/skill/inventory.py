"""业务技能清单与版本治理（路线图 P2.5）。

业务技能的执行仍走受控目录（octop_bridge 校验目录/后缀/越界），本模块提供：
- ``collect_inventory``：扫描合法技能包，计算每包的版本哈希（按文件内容排序哈希）；
- ``sync_manifest``：把清单持久化为版本化 manifest（发布/锁定），支持漂移校验；
- 对外只读：``GET /api/skills/business-inventory``（需 skills.view）。

边界：本模块不把技能正文写入数据库、不改变执行路径；manifest 是审计/发布/回滚依据。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

MANIFEST_PATH = PROJECT_ROOT / "storage" / "business_skills_inventory.json"
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_inventory(root: Path | None = None) -> list[dict]:
    """扫描业务技能包，返回带版本哈希与资源清单的目录项；损坏/停用包记为 invalid。"""
    from app.modules.octop_bridge.skills import BUSINESS_SKILLS_ROOT, list_skills
    directory = (Path(root) if root is not None else BUSINESS_SKILLS_ROOT).resolve()
    valid = {item["skill_name"]: item for item in list_skills(root=directory)}
    items = []
    for entry in sorted(directory.iterdir(), key=lambda path: path.name.casefold()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        manifest = valid.get(entry.name)
        if manifest is None:
            items.append({"skill_name": entry.name, "status": "invalid",
                          "version": None, "resources": [], "name": entry.name,
                          "description": "", "reason": "清单缺失、损坏或已停用"})
            continue
        resources = []
        for resource in sorted(entry.rglob("*")):
            if resource.is_file() and resource.relative_to(entry).parts[0] != ".":
                rel = resource.relative_to(entry).as_posix()
                resources.append({"path": rel, "size": resource.stat().st_size,
                                  "sha256": _file_sha256(resource)})
        # 包版本：完整文件指纹的有序聚合。
        fingerprint = hashlib.sha256()
        for resource in resources:
            fingerprint.update(f"{resource['path']}|{resource['sha256']}\n".encode("utf-8"))
        items.append({
            "skill_name": entry.name, "status": "valid",
            "name": manifest.get("name") or entry.name,
            "description": manifest.get("description") or "",
            "version": fingerprint.hexdigest(),
            "resources": resources,
        })
    return items


def _manifest_version(inventory: list[dict]) -> str:
    digest = hashlib.sha256()
    for item in sorted(inventory, key=lambda entry: entry["skill_name"]):
        digest.update(f"{item['skill_name']}|{item.get('version') or 'invalid'}\n".encode("utf-8"))
    return digest.hexdigest()


def read_manifest(path: Path | None = None) -> dict:
    """读当前 manifest；不存在或损坏返回空结构（不抛异常）。"""
    target = Path(path) if path is not None else MANIFEST_PATH
    if not target.is_file():
        return {"version": None, "syncedAt": None, "skills": {}}
    try:
        if target.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("manifest 超过大小上限")
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("skills"), dict):
            raise ValueError("manifest 结构无效")
        return payload
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("business skill manifest 读取失败: %s", exc)
        return {"version": None, "syncedAt": None, "skills": {}}


def build_manifest(inventory: list[dict]) -> dict:
    """由当前目录生成新 manifest（纯函数，不落盘）。"""
    return {
        "version": _manifest_version(inventory),
        "syncedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "skills": {item["skill_name"]: {
            "name": item["name"], "description": item["description"],
            "status": item["status"], "version": item.get("version"),
            "reason": item.get("reason"), "resource_count": len(item.get("resources") or []),
            "resource_sha256": {res["path"]: res["sha256"] for res in (item.get("resources") or [])},
        } for item in inventory},
    }


def sync_manifest(inventory: list[dict], *, path: Path | None = None, write: bool = True) -> dict:
    """把当前清单与已有 manifest 对比，返回差异；write=True 时原子写新 manifest。"""
    target = Path(path) if path is not None else MANIFEST_PATH
    previous = read_manifest(target)
    manifest = build_manifest(inventory)
    previous_skills = previous.get("skills") or {}
    changed = [name for name, detail in manifest["skills"].items()
               if previous_skills.get(name, {}).get("version") != detail.get("version")]
    removed = [name for name in previous_skills if name not in manifest["skills"]]
    if write:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    return {"changed": changed, "removed": removed, "version": manifest["version"],
            "previous_version": previous.get("version"), "dry_run": not write}


def drift(inventory: list[dict], *, path: Path | None = None) -> dict:
    """对比内存清单与已发布 manifest，返回漂移（真库与发布版本不一致项）。"""
    target = Path(path) if path is not None else MANIFEST_PATH
    manifest = read_manifest(target)
    expected = {item["skill_name"]: item.get("version") for item in inventory}
    published = {name: detail.get("version") for name, detail in (manifest.get("skills") or {}).items()}
    drifted = sorted(name for name in expected if expected.get(name) != published.get(name)) + \
              sorted(name for name in published if name not in expected)
    return {"drifted": drifted, "manifest_version": manifest.get("version"),
            "expected_version": _manifest_version(inventory) if inventory else None}