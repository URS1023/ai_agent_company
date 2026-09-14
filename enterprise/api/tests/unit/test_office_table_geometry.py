"""A short continuation table must not stretch its rows to fill the old frame."""

import pytest
from lxml import etree

from enterprise_platform.adapters.office_table_geometry import compact_table_rows
from enterprise_platform.application.errors import InvalidInput

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def slide():
    root = etree.Element(P + "sld")
    tree = etree.SubElement(etree.SubElement(root, P + "cSld"), P + "spTree")
    frame = etree.SubElement(tree, P + "graphicFrame")
    props = etree.SubElement(frame, P + "nvGraphicFramePr")
    etree.SubElement(props, P + "cNvPr", id="7", name="Equipment table")
    transform = etree.SubElement(frame, P + "xfrm")
    etree.SubElement(transform, A + "off", x="640080", y="1463040")
    etree.SubElement(transform, A + "ext", cx="10927080", cy="4526280")
    table = etree.SubElement(etree.SubElement(etree.SubElement(frame, A + "graphic"), A + "graphicData"), A + "tbl")
    for value in ("Equipment", "ROW_16"):
        row = etree.SubElement(table, A + "tr", h="2263140")
        cell = etree.SubElement(row, A + "tc")
        paragraph = etree.SubElement(etree.SubElement(cell, A + "txBody"), A + "p")
        etree.SubElement(etree.SubElement(paragraph, A + "r"), A + "t").text = value
    return etree.tostring(root)


def test_compacts_rows_and_frame_together_without_changing_text_position_or_width():
    original = etree.fromstring(slide())
    output = etree.fromstring(compact_table_rows(slide(), shape_id=7, row_heights=(502920, 502920)))
    rows = output.findall(f".//{A}tbl/{A}tr")
    assert [row.get("h") for row in rows] == ["502920", "502920"]
    extent = output.find(f".//{P}xfrm/{A}ext")
    assert extent.get("cy") == "1005840"
    for row in rows:
        row.set("h", "2263140")
    extent.set("cy", "4526280")
    assert etree.tostring(output) == etree.tostring(original)


@pytest.mark.parametrize("heights", [(0, 1), (-1, 1), (True, 1), (1.5, 1), (), (1,), (4526280, 1)])
def test_rejects_invalid_row_counts_heights_or_expansion(heights):
    with pytest.raises(InvalidInput):
        compact_table_rows(slide(), shape_id=7, row_heights=heights)


@pytest.mark.parametrize("shape_id", [0, -1, True, 8])
def test_requires_exact_native_shape_identity(shape_id):
    with pytest.raises(InvalidInput):
        compact_table_rows(slide(), shape_id=shape_id, row_heights=(502920, 502920))


def test_rejects_ambiguous_duplicate_shape_identity():
    root = etree.fromstring(slide())
    tree = root.find(f"{P}cSld/{P}spTree")
    tree.append(etree.fromstring(etree.tostring(tree[0])))
    with pytest.raises(InvalidInput):
        compact_table_rows(etree.tostring(root), shape_id=7, row_heights=(502920, 502920))
