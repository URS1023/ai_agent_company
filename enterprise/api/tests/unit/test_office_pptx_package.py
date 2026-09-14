"""Install chart and workbook replacements together, preserving unrelated parts."""

from decimal import Decimal
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from enterprise_platform.adapters.office_pptx_package import ChartBinding, assemble_chart_data
from enterprise_platform.application.errors import InvalidInput
from enterprise_platform.domain.office_content import ChartData, ChartSeries

C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P = "{http://schemas.openxmlformats.org/package/2006/relationships}"
PART = "ppt/charts/chart1.xml"
BOOK = "ppt/embeddings/Microsoft_Excel_Sheet1.xlsx"


def binding():
    return ChartBinding(
        PART,
        ChartData(
            categories=("A", "B"), series=(ChartSeries(name="Values", values=(Decimal("9007199254740993"), None)),)
        ),
    )


def fixture(*, target="../embeddings/Microsoft_Excel_Sheet1.xlsx", external=False, extra_chart=False):
    root = ET.Element(C + "chartSpace")
    chart = ET.SubElement(root, C + "chart")
    series = ET.SubElement(ET.SubElement(ET.SubElement(chart, C + "plotArea"), C + "barChart"), C + "ser")
    for name in ("idx", "order"):
        ET.SubElement(series, C + name, val="0")
    for name in ("tx", "cat", "val"):
        ET.SubElement(series, C + name)
    ET.SubElement(root, C + "externalData", {R + "id": "rId1"})
    rels = ET.Element(P + "Relationships")
    attrs = {"Id": "rId1", "Type": R[1:-1] + "/package", "Target": target}
    if external:
        attrs["TargetMode"] = "External"
    ET.SubElement(rels, P + "Relationship", attrs)
    members = {
        "[Content_Types].xml": b"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="xlsx" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"/>
          <Override PartName="/ppt/presentation.xml"
            ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
          </Types>""",
        "ppt/presentation.xml": b"<presentation/>",
        "ppt/slides/slide1.xml": b"<unchanged-slide/>",
        "ppt/media/image1.png": b"unchanged-image-bytes",
        PART: ET.tostring(root),
        "ppt/charts/_rels/chart1.xml.rels": ET.tostring(rels),
        BOOK: b"old-generated-workbook",
    }
    if extra_chart:
        members["ppt/charts/chart2.xml"] = members[PART]
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as package:
        package.comment = b"original package comment"
        for name, value in members.items():
            package.writestr(name, value)
    return output.getvalue()


def test_installs_both_parts_and_preserves_every_other_member():
    original = fixture()
    result = assemble_chart_data(original, (binding(),))
    with ZipFile(BytesIO(original)) as old, ZipFile(BytesIO(result)) as new:
        assert new.testzip() is None
        assert new.namelist() == old.namelist()
        assert new.comment == old.comment
        for name in old.namelist():
            if name not in {PART, BOOK}:
                assert new.read(name) == old.read(name)
        chart = ET.fromstring(new.read(PART))
        values = chart.findall(f".//{C}numCache/{C}pt/{C}v")
        assert [value.text for value in values] == ["9007199254740993"]
        with ZipFile(BytesIO(new.read(BOOK))) as workbook:
            assert workbook.testzip() is None


@pytest.mark.parametrize(
    "target,external",
    [
        ("https://example.invalid/book.xlsx", True),
        ("../../../outside.xlsx", False),
        ("../embeddings/missing.xlsx", False),
        ("../embeddings/Microsoft_Excel_Sheet1.xlsx#fragment", False),
    ],
)
def test_rejects_external_or_missing_workbook_targets(target, external):
    with pytest.raises(InvalidInput):
        assemble_chart_data(fixture(target=target, external=external), (binding(),))


@pytest.mark.parametrize(
    "bindings", [(), (binding(), binding()), (ChartBinding("ppt/charts/unknown.xml", binding().data),)]
)
def test_requires_exact_complete_chart_bindings(bindings):
    with pytest.raises(InvalidInput):
        assemble_chart_data(fixture(), bindings)


def test_unbound_second_chart_is_not_silently_left_with_stale_values():
    with pytest.raises(InvalidInput):
        assemble_chart_data(fixture(extra_chart=True), (binding(),))


def test_malformed_zip_produces_domain_error():
    with pytest.raises(InvalidInput):
        assemble_chart_data(b"not a zip", (binding(),))


@pytest.mark.parametrize("shared", [False, True])
def test_two_charts_update_distinct_workbooks_or_reject_a_shared_target(shared):
    output = BytesIO()
    second_book = "ppt/embeddings/Microsoft_Excel_Sheet2.xlsx"
    with ZipFile(BytesIO(fixture())) as source, ZipFile(output, "w") as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr("ppt/charts/chart2.xml", source.read(PART))
        rels = source.read("ppt/charts/_rels/chart1.xml.rels")
        if not shared:
            rels = rels.replace(b"Microsoft_Excel_Sheet1.xlsx", b"Microsoft_Excel_Sheet2.xlsx")
            target.writestr(second_book, b"second-generated-workbook")
        target.writestr("ppt/charts/_rels/chart2.xml.rels", rels)
    second = ChartBinding(
        "ppt/charts/chart2.xml",
        ChartData(categories=("C",), series=(ChartSeries(name="Other", values=(Decimal("1.2300"),)),)),
    )
    if shared:
        with pytest.raises(InvalidInput):
            assemble_chart_data(output.getvalue(), (binding(), second))
        return
    result = assemble_chart_data(output.getvalue(), (second, binding()))
    with ZipFile(BytesIO(result)) as package:
        assert ET.fromstring(package.read("ppt/charts/chart2.xml")).findtext(f".//{C}numCache/{C}pt/{C}v") == "1.2300"
        assert ET.fromstring(package.read(PART)).findtext(f".//{C}numCache/{C}pt/{C}v") == "9007199254740993"
        for name in (BOOK, second_book):
            with ZipFile(BytesIO(package.read(name))) as workbook:
                assert workbook.testzip() is None


def test_duplicate_zip_names_are_rejected():
    output = BytesIO(fixture())
    with ZipFile(output, "a") as package, pytest.warns(UserWarning, match="Duplicate name"):
        package.writestr(PART, b"duplicate")
    with pytest.raises(InvalidInput):
        assemble_chart_data(output.getvalue(), (binding(),))


def test_package_size_limit_is_enforced(monkeypatch):
    monkeypatch.setattr("enterprise_platform.adapters.office_pptx_package._PACKAGE_LIMIT", 16)
    with pytest.raises(InvalidInput):
        assemble_chart_data(fixture(), (binding(),))


@pytest.mark.parametrize("declaration", ["Override", "Default"])
def test_declared_chart_with_nonstandard_name_must_not_be_ignored(declaration):
    original = fixture()
    output = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(output, "w") as target:
        types = ET.fromstring(source.read("[Content_Types].xml"))
        part_name = "ppt/charts/custom.chart"
        attributes = {"PartName": "/" + part_name} if declaration == "Override" else {"Extension": "chart"}
        ET.SubElement(
            types,
            "{http://schemas.openxmlformats.org/package/2006/content-types}" + declaration,
            attributes,
            ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml",
        )
        for name in source.namelist():
            target.writestr(name, ET.tostring(types) if name == "[Content_Types].xml" else source.read(name))
        target.writestr(part_name, source.read(PART))
    with pytest.raises(InvalidInput):
        assemble_chart_data(output.getvalue(), (binding(),))
