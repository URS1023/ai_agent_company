# -*- coding: utf-8 -*-
"""Build the twenty rich sample decks and their real three-slide previews."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.common.storage import get_storage
from app.db import new_session
from app.models import File as FileRow
from app.modules.artifact.renderers import render_presentation
from app.modules.presentation.preview import (
    BUILTIN_PREVIEW_DIR,
    cache_custom_preview,
    custom_preview_key,
    render_pptx_preview_sheet,
)
from app.modules.presentation.premium_demo import (
    build_demo_spec,
    preview_slide_numbers,
)
from app.modules.presentation.service import TEMPLATES

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "presentation-templates"


def main() -> None:
    BUILTIN_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    EXAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    valid_ids = {template["id"] for template in TEMPLATES}
    for old in BUILTIN_PREVIEW_DIR.glob("*.webp"):
        if old.stem not in valid_ids:
            old.unlink()
    for old in EXAMPLE_DIR.glob("*.pptx"):
        if old.stem not in valid_ids:
            old.unlink()

    for index, template in enumerate(TEMPLATES, start=1):
        spec = build_demo_spec(template)
        pptx = render_presentation(spec)
        example_path = EXAMPLE_DIR / f"{template['id']}.pptx"
        example_path.write_bytes(pptx)
        image = render_pptx_preview_sheet(
            pptx, slide_numbers=preview_slide_numbers(template["id"])
        )
        if not image:
            raise RuntimeError(f"无法渲染模板多页预览：{template['id']}")
        preview_path = BUILTIN_PREVIEW_DIR / f"{template['id']}.webp"
        preview_path.write_bytes(image)
        print(
            f"[{index:02d}/{len(TEMPLATES)}] {template['id']} "
            f"pptx={len(pptx)} preview={len(image)}",
            flush=True,
        )

    storage = get_storage()
    db = new_session()
    custom_generated = 0
    try:
        rows = db.execute(select(FileRow).where(
            FileRow.source_type == "presentation_template",
            FileRow.status == "ready",
            FileRow.deleted_at.is_(None),
        )).scalars().all()
        for row in rows:
            if storage.exists(custom_preview_key(row.storage_key)):
                continue
            if cache_custom_preview(
                storage, row.storage_key, storage.read_bytes(row.storage_key)
            ):
                custom_generated += 1
    finally:
        db.close()
    print(
        f"generated {len(TEMPLATES)} rich decks and previews; "
        f"backfilled {custom_generated} customer previews"
    )


if __name__ == "__main__":
    main()
