import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from enterprise_platform.adapters.dashboard_templates import FileDashboardTemplates
from enterprise_platform.application.errors import DependencyUnavailable, NotFound
from enterprise_platform.domain.dashboard import DesignSnapshot


def write_catalog(path: Path, templates: list[dict[str, object]]) -> str:
    content = json.dumps({"schema_version": 1, "templates": templates}).encode()
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def template() -> dict[str, object]:
    return asdict(DesignSnapshot("equipment", 1, 1, '{"widgets":[]}', "a" * 64, "lynx-series-v1"))


def test_catalog_loads_detached_snapshots_and_rejects_unknown_id(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    digest = write_catalog(path, [template()])
    catalog = FileDashboardTemplates(path, digest)
    assert [item.template_id for item in catalog.list()] == ["equipment"]
    first = catalog.get("equipment")
    first.visual["widgets"] = ["changed"]
    assert catalog.get("equipment").visual == {"widgets": []}
    with pytest.raises(NotFound):
        catalog.get("unknown")


def test_missing_or_changed_catalog_has_no_stale_fallback(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    catalog = FileDashboardTemplates(path, "a" * 64)
    with pytest.raises(DependencyUnavailable):
        catalog.list()
    digest = write_catalog(path, [template()])
    catalog = FileDashboardTemplates(path, digest)
    assert len(catalog.list()) == 1
    path.write_text("changed")
    with pytest.raises(DependencyUnavailable):
        catalog.get("equipment")


@pytest.mark.parametrize(
    "templates",
    [
        [template(), template()],
        [{**template(), "sql": "SELECT 1"}],
        [{**template(), "visual_json": "[]"}],
        [{**template(), "template_revision": 0}],
    ],
)
def test_invalid_catalog_is_opaque(tmp_path: Path, templates: list[dict[str, object]]) -> None:
    path = tmp_path / "catalog.json"
    catalog = FileDashboardTemplates(path, write_catalog(path, templates))
    with pytest.raises(DependencyUnavailable):
        catalog.list()
