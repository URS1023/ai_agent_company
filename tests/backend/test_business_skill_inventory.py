"""业务技能清单/版本治理测试（P2.5；纯临时目录，不触业务库）。"""
import json
from pathlib import Path

from app.modules.skill import inventory


def _seed_skill(root: Path, name: str, body: str = "# 技能\n描述与正文。") -> None:
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} 技能描述\n---\n{body}", encoding="utf-8")


def test_collect_inventory_lists_valid_skills_with_version(tmp_path):
    _seed_skill(tmp_path, "alpha")
    _seed_skill(tmp_path, "beta")
    items = inventory.collect_inventory(root=tmp_path)
    assert {item["skill_name"] for item in items} == {"alpha", "beta"}
    for item in items:
        assert item["status"] == "valid"
        assert item["version"] and len(item["version"]) == 64
        assert any(res["path"] == "SKILL.md" for res in item["resources"])


def test_collect_inventory_marks_invalid_package(tmp_path):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "README.txt").write_text("缺 SKILL.md", encoding="utf-8")
    items = inventory.collect_inventory(root=tmp_path)
    broken = next(item for item in items if item["skill_name"] == "broken")
    assert broken["status"] == "invalid" and broken["version"] is None


def test_version_changes_when_content_changes(tmp_path):
    _seed_skill(tmp_path, "alpha", "v1")
    first = inventory.collect_inventory(root=tmp_path)[0]["version"]
    (tmp_path / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: 描述\n---\nv2", encoding="utf-8")
    second = inventory.collect_inventory(root=tmp_path)[0]["version"]
    assert first != second


def test_sync_and_drift_detection(tmp_path):
    _seed_skill(tmp_path, "alpha")
    items = inventory.collect_inventory(root=tmp_path)
    manifest_path = tmp_path / "manifest.json"
    result = inventory.sync_manifest(items, path=manifest_path, write=True)
    assert result["changed"] == ["alpha"] and result["removed"] == []
    assert manifest_path.is_file()

    # 内容不变 → 无漂移
    again = inventory.collect_inventory(root=tmp_path)
    assert inventory.drift(again, path=manifest_path)["drifted"] == []

    # 内容变化 → 漂移
    (tmp_path / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: 描述\n---\nchanged", encoding="utf-8")
    drifted_items = inventory.collect_inventory(root=tmp_path)
    assert inventory.drift(drifted_items, path=manifest_path)["drifted"] == ["alpha"]


def test_manifest_round_trip_preserves_published_state(tmp_path):
    _seed_skill(tmp_path, "alpha")
    items = inventory.collect_inventory(root=tmp_path)
    manifest_path = tmp_path / "manifest.json"
    inventory.sync_manifest(items, path=manifest_path, write=True)
    loaded = inventory.read_manifest(manifest_path)
    assert loaded["version"] and loaded["skills"]["alpha"]["version"] == items[0]["version"]


def test_corrupt_manifest_reads_as_empty(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{not json", encoding="utf-8")
    assert inventory.read_manifest(manifest_path)["skills"] == {}