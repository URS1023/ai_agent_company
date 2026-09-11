"""Operator-pinned exported template catalog; no visual documents from API callers.

Every access verifies current bytes against the configured catalog digest. Updating
a deployed template requires explicitly replacing the trusted digest as well as
the file. This adapter validates documents, not deployment asset availability.
"""

import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import Field

from enterprise_platform.application.errors import DependencyUnavailable, NotFound
from enterprise_platform.domain import dashboard as d


class _Template(d.Contract):
    template_id: str
    template_revision: int
    design_revision: int
    visual_json: str
    renderer_build_id: str
    component_schema_version: str
    asset_digests: tuple[d.ResourceDigest, ...]
    font_digests: tuple[d.ResourceDigest, ...]
    slots: tuple[d.SlotContract, ...]

    def snapshot(self) -> d.DesignSnapshot:
        return d.DesignSnapshot(
            self.template_id,
            self.template_revision,
            self.design_revision,
            self.visual_json,
            self.renderer_build_id,
            self.component_schema_version,
            self.asset_digests,
            self.font_digests,
            self.slots,
        )


class _Catalog(d.Contract):
    schema_version: Literal[1]
    templates: tuple[_Template, ...] = Field(min_length=1, max_length=100)


class FileDashboardTemplates:
    def __init__(self, path: Path, sha256: str) -> None:
        if not path.is_absolute() or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ValueError("Template catalog requires an absolute path and SHA256")
        self._path, self._sha256 = path, sha256

    def list(self) -> tuple[d.DesignSnapshot, ...]:
        try:
            with self._path.open("rb") as stream:
                content = stream.read(8 * 1024 * 1024 + 1)
            if len(content) > 8 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != self._sha256:
                raise ValueError("Catalog digest mismatch")
            document = _Catalog.model_validate_json(content, strict=True)
            snapshots = tuple(item.snapshot() for item in document.templates)
            if len({item.template_id for item in snapshots}) != len(snapshots):
                raise ValueError("Duplicate template")
            return snapshots
        except (OSError, ValueError):
            raise DependencyUnavailable("dashboard_template_catalog_unavailable") from None

    def get(self, template_id: str) -> d.DesignSnapshot:
        for template in self.list():
            if template.template_id == template_id:
                return template
        raise NotFound("dashboard_template_not_found")
