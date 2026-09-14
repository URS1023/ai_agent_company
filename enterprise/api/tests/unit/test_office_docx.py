"""Inspect real DOCX packages rather than substituting a mock renderer."""

import io
import json
import zipfile
from decimal import Decimal
from uuid import UUID
from xml.etree import ElementTree as ET

import pytest
from docx import Document

from enterprise_platform.adapters.office_docx import render_office_docx
from enterprise_platform.application.errors import InvalidInput
from enterprise_platform.application.office_edits import OfficeFileRecord
from enterprise_platform.domain.office_content import TableData
from enterprise_platform.domain.office_revision import OfficeRevision, OfficeText, OfficeUnit

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def record():
    return OfficeFileRecord(
        "workspace",
        "document-default",
        1,
        ("snapshot-1",),
        OfficeRevision(
            file_id=UUID(int=1),
            revision=3,
            kind="document",
            units=(
                OfficeUnit(unit_id=UUID(int=2), kind="paragraph", content=(OfficeText(text="设备质检报告"),)),
                OfficeUnit(
                    unit_id=UUID(int=3),
                    kind="table",
                    content=(
                        TableData(
                            table_id="measurements",
                            headers=("设备", "结果", "缺失"),
                            rows=(("0007", Decimal("1.2300000000000000001"), None), ("0008", 9007199254740993, "")),
                        ),
                    ),
                ),
            ),
        ),
    )


def test_real_docx_retains_complete_exact_table_and_stable_unit_tags():
    data = render_office_docx(record())
    assert Document(io.BytesIO(data)).core_properties.identifier == "office:00000000-0000-0000-0000-000000000001:r3"
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        assert package.testzip() is None
        root = ET.fromstring(package.read("word/document.xml"))
        controls = root.findall(f"{W}body/{W}sdt")
        assert [s.find(f"{W}sdtPr/{W}tag").get(W + "val") for s in controls] == [str(UUID(int=2)), str(UUID(int=3))]
        text = [node.text for node in root.iter(W + "t")]
        assert "设备质检报告" in text
        assert "0007" in text
        assert "1.2300000000000000001" in text
        assert "9007199254740993" in text
        assert len(list(root.iter(W + "tr"))) == 3
        metadata = ET.fromstring(package.read("customXml/enterprise-office.xml"))
        payload = json.loads(metadata.text)
        assert payload["file_fingerprint"] == record().fingerprint()
        assert payload["source_snapshot_ids"] == ["snapshot-1"]
        assert payload["content"]["revision"] == 3
        assert payload["content"]["units"][1]["content"][0]["rows"][0][2] is None


def test_docx_rejects_presentation_instead_of_silently_dropping_it():
    from dataclasses import replace

    original = record()
    presentation = OfficeRevision(
        file_id=UUID(int=1),
        revision=1,
        kind="presentation",
        units=(OfficeUnit(unit_id=UUID(int=2), kind="slide", content=(OfficeText(text="Slide"),)),),
    )
    with pytest.raises(InvalidInput):
        render_office_docx(replace(original, content=presentation))


def test_local_edit_changes_only_selected_docx_content_control():
    from dataclasses import replace

    from enterprise_platform.domain.office_revision import UnitReplacement, replace_units

    original = record()
    changed = replace(
        original,
        content=replace_units(
            original.content,
            file_id=original.content.file_id,
            expected_revision=3,
            replacements=(UnitReplacement(unit_id=UUID(int=2), content=(OfficeText(text="修改后的质检结论"),)),),
        ),
    )

    def controls(data):
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            root = ET.fromstring(package.read("word/document.xml"))
            return [ET.tostring(node) for node in root.findall(f"{W}body/{W}sdt")]

    before, after = controls(render_office_docx(original)), controls(render_office_docx(changed))
    assert before[0] != after[0]
    assert before[1] == after[1]


def test_all_table_rows_are_exported_without_page_size_truncation():
    from dataclasses import replace

    original = record()
    rows = tuple((str(i).zfill(4), i, None) for i in range(53))
    table_unit = OfficeUnit(
        unit_id=UUID(int=3),
        kind="table",
        content=(TableData(table_id="measurements", headers=("设备", "数值", "缺失"), rows=rows),),
    )
    updated = replace(original, content=original.content.model_copy(update={"units": (table_unit,)}))
    with zipfile.ZipFile(io.BytesIO(render_office_docx(updated))) as package:
        root = ET.fromstring(package.read("word/document.xml"))
        assert len(list(root.iter(W + "tr"))) == 54
        assert "0052" in [node.text for node in root.iter(W + "t")]


def test_invalid_xml_text_is_reported_as_domain_error_not_partial_artifact():
    from dataclasses import replace

    original = record()
    unit = OfficeUnit(unit_id=UUID(int=2), kind="paragraph", content=(OfficeText(text="invalid\x00text"),))
    invalid = replace(original, content=original.content.model_copy(update={"units": (unit,)}))
    with pytest.raises(InvalidInput, match="^office_document_text_invalid$"):
        render_office_docx(invalid)


@pytest.mark.parametrize("field", ["template_id", "table_id", "source_snapshot_ids"])
def test_nonvisible_metadata_rejects_invalid_xml_and_surrogate_text(field):
    from dataclasses import replace

    original = record()
    if field == "template_id":
        invalid = replace(original, template_id="template\ufffe")
    elif field == "source_snapshot_ids":
        invalid = replace(original, source_snapshot_ids=("snapshot\ud800",))
    else:
        table = original.content.units[1].content[0].model_copy(update={"table_id": "table\ufffe"})
        unit = original.content.units[1].model_copy(update={"content": (table,)})
        invalid = replace(original, content=original.content.model_copy(update={"units": (unit,)}))
    with pytest.raises(InvalidInput, match="^office_document_text_invalid$"):
        render_office_docx(invalid)


def test_table_header_repeats_on_continuation_pages_without_marking_data_rows():
    with zipfile.ZipFile(io.BytesIO(render_office_docx(record()))) as package:
        root = ET.fromstring(package.read("word/document.xml"))
        rows = list(root.iter(W + "tr"))
        header = rows[0].find(f"{W}trPr/{W}tblHeader")
        assert header is not None
        assert header.get(W + "val") == "true"
        assert all(row.find(f"{W}trPr/{W}tblHeader") is None for row in rows[1:])
        assert len(rows) == 3
