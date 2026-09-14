"""Packaged legacy PPT designs retain their original identity and remain immutable."""

import pytest
from pydantic import ValidationError

from enterprise_platform.adapters.office_templates import builtin_templates, get_builtin_template
from enterprise_platform.application.errors import NotFound


@pytest.mark.parametrize(
    "template_id,revision,allowed",
    [
        ("document-default", 1, True),
        ("document-default", True, False),
        ("document-default", 2, False),
        ("executive-ivory", 1, False),
        ("missing", 1, False),
    ],
)
def test_document_template_binding_is_exact(template_id, revision, allowed):
    from dataclasses import replace

    from test_office_export import setup

    from enterprise_platform.adapters.office_templates import require_office_template

    record = replace(setup()[-1], template_id=template_id, template_revision=revision)
    if allowed:
        require_office_template(record)
    else:
        with pytest.raises(NotFound):
            require_office_template(record)


def test_presentation_creation_accepts_all_twenty_original_templates():
    from dataclasses import replace

    from test_office_export import setup

    from enterprise_platform.adapters.office_templates import require_office_template
    from enterprise_platform.domain.office_revision import OfficeRevision, OfficeUnit

    original = setup()[-1]
    content = OfficeRevision(
        file_id=original.content.file_id,
        revision=1,
        kind="presentation",
        units=(
            OfficeUnit(
                unit_id=original.content.units[0].unit_id, kind="slide", content=original.content.units[0].content
            ),
        ),
    )
    for template in builtin_templates():
        require_office_template(replace(original, content=content, template_id=template.id))
    with pytest.raises(NotFound):
        require_office_template(replace(original, content=content))


def test_twenty_original_designs_are_available_with_preserved_chinese_metadata():
    templates = builtin_templates()
    assert len(templates) == len({t.id for t in templates}) == 20
    first = get_builtin_template("executive-ivory", 1)
    assert first.name == "象牙高管"
    assert first.theme.accent == "183A5A"
    assert first.design.cover == "split"
    assert first.typography.title_font == "Microsoft YaHei UI"
    assert len({t.design.model_dump_json() for t in templates}) == 20
    assert all("table" in t.layouts and "chart" in t.layouts for t in templates)


def test_template_configuration_is_deeply_immutable():
    template = get_builtin_template("executive-ivory", 1)
    with pytest.raises(ValidationError):
        template.theme.accent = "FFFFFF"
    with pytest.raises(ValidationError):
        template.typography.title_size = 99
    assert isinstance(template.layouts, tuple)
    assert get_builtin_template("executive-ivory", 1).theme.accent == "183A5A"


@pytest.mark.parametrize(
    "template_id,revision",
    [("missing", 1), ("executive-ivory", 2), ("executive-ivory", True), ("executive-ivory", 1.0), ("../catalog", 1)],
)
def test_unknown_or_wrong_revision_never_falls_back(template_id, revision):
    with pytest.raises(NotFound):
        get_builtin_template(template_id, revision)


@pytest.mark.parametrize("mutation", ["palette", "duplicate", "provenance", "extra_field"])
def test_tampered_packaged_catalog_fails_closed(tmp_path, monkeypatch, mutation):
    import json
    from importlib.resources import files

    from enterprise_platform.adapters import office_templates
    from enterprise_platform.application.errors import PersistenceError

    data = json.loads(files("enterprise_platform").joinpath("resources/office_builtin_templates.json").read_bytes())
    if mutation == "palette":
        data["templates"][0]["theme"]["accent"] = "FFFFFF"
    elif mutation == "duplicate":
        data["templates"][1] = data["templates"][0]
    elif mutation == "provenance":
        data["source_sha256"] = "0" * 64
    else:
        data["templates"][0]["remote_url"] = "https://invalid.example/template"
    directory = tmp_path / "resources"
    directory.mkdir()
    (directory / "office_builtin_templates.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(office_templates, "files", lambda package: tmp_path)
    with pytest.raises(PersistenceError, match="^office_template_catalog_invalid$"):
        office_templates.builtin_templates()
