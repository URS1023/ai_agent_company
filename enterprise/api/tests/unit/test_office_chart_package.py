"""Exact native chart caches and their editable workbook must agree."""

from decimal import Decimal
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from lxml import etree
from pydantic import ValidationError

from enterprise_platform.adapters.office_chart_package import restore_chart_data
from enterprise_platform.adapters.office_templates import get_builtin_template
from enterprise_platform.application.errors import InvalidInput
from enterprise_platform.domain.office_content import ChartData, ChartSeries

C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def chart_xml(series_count=1):
    root = ET.Element(C + "chartSpace")
    chart = ET.SubElement(root, C + "chart")
    plot = ET.SubElement(ET.SubElement(chart, C + "plotArea"), C + "barChart")
    for i in range(series_count):
        series = ET.SubElement(plot, C + "ser")
        ET.SubElement(series, C + "idx", val=str(i))
        ET.SubElement(series, C + "order", val=str(i))
        ET.SubElement(series, C + "tx")
        style = ET.SubElement(series, C + "spPr")
        ET.SubElement(style, "{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill")
        ET.SubElement(series, C + "cat")
        ET.SubElement(series, C + "val")
    ET.SubElement(chart, C + "dispBlanksAs", val="zero")
    return ET.tostring(root)


def source():
    return ChartData(
        categories=("missing", "large", "fraction"),
        series=(
            ChartSeries(
                name="Measurements", values=(None, Decimal("9007199254740993"), Decimal("0.1234567890123456789"))
            ),
        ),
    )


def test_chart_cache_and_editable_workbook_retain_exact_values_and_missing_points():
    parts = restore_chart_data(chart_xml(), source())
    root = ET.fromstring(parts.chart_xml)
    cache = root.find(f".//{C}val/{C}numRef/{C}numCache")
    assert cache.find(C + "ptCount").get("val") == "3"
    assert [(pt.get("idx"), pt.findtext(C + "v")) for pt in cache.findall(C + "pt")] == [
        ("1", "9007199254740993"),
        ("2", "0.1234567890123456789"),
    ]
    assert root.find(f"{C}chart/{C}dispBlanksAs").get("val") == "gap"
    assert root.find(f".//{C}ser/{C}spPr") is not None
    with ZipFile(BytesIO(parts.workbook)) as package:
        assert package.testzip() is None
        sheet = ET.fromstring(package.read("xl/worksheets/sheet1.xml"))
        cells = {cell.get("r"): cell for cell in sheet.iter(S + "c")}
        assert "B2" not in cells
        assert cells["B3"].findtext(S + "v") == "9007199254740993"
        assert cells["B4"].findtext(S + "v") == "0.1234567890123456789"
        assert cells["B1"].findtext(f"{S}is/{S}t") == "Measurements"


def test_multiple_series_and_literal_formula_like_labels_are_not_executable_formulas():
    data = ChartData(
        categories=("=1+1", "中文 & < >"),
        series=(
            ChartSeries(name='=HYPERLINK("x")', values=(Decimal("0.00"), None)),
            ChartSeries(name="Second", values=(Decimal("-1.50"), Decimal("2E+10"))),
        ),
    )
    result = restore_chart_data(chart_xml(2), data)
    root = ET.fromstring(result.chart_xml)
    assert [node.text for node in root.findall(f".//{C}val/{C}numRef/{C}f")] == ["Sheet1!$B$2:$B$3", "Sheet1!$C$2:$C$3"]
    with ZipFile(BytesIO(result.workbook)) as package:
        sheet = ET.fromstring(package.read("xl/worksheets/sheet1.xml"))
        assert not list(sheet.iter(S + "f"))
        assert "=1+1" in [node.text for node in sheet.iter(S + "t")]
        assert "0.00" in [node.text for node in sheet.iter(S + "v")]


@pytest.mark.parametrize("payload", [b"broken", b"<chartSpace/>", chart_xml(2)])
def test_incompatible_chart_is_rejected_without_partial_output(payload):
    with pytest.raises(InvalidInput):
        restore_chart_data(payload, source())


@pytest.mark.parametrize("label", ["bad\x00", "bad\ufffe"])
def test_invalid_xml_labels_are_rejected_before_serialization(label):
    data = ChartData(categories=(label,), series=(ChartSeries(name="x", values=(Decimal(1),)),))
    with pytest.raises(InvalidInput):
        restore_chart_data(chart_xml(), data)


def test_surrogate_label_is_rejected_by_the_domain_before_rendering():
    with pytest.raises(ValidationError):
        ChartData(categories=("bad\ud800",), series=(ChartSeries(name="x", values=(Decimal(1),)),))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le"])
def test_entity_definitions_are_not_processed_in_any_supported_input(encoding):
    payload = '<!DOCTYPE chartSpace [<!ENTITY value "x">]><chartSpace>&value;</chartSpace>'
    with pytest.raises(InvalidInput):
        restore_chart_data(payload.encode(encoding), source())


def test_markup_compatibility_style_prefixes_remain_bound():
    root = etree.fromstring(chart_xml())
    markup = etree.fromstring(b"""<mc:AlternateContent
        xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
        xmlns:c14="http://schemas.microsoft.com/office/drawing/2007/8/2/chart"
        mc:Ignorable="c14"><mc:Choice Requires="c14"><c14:style val="102"/></mc:Choice>
        <mc:Fallback/></mc:AlternateContent>""")
    root.append(markup)
    result = etree.fromstring(restore_chart_data(etree.tostring(root), source()).chart_xml)
    mc = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
    alternate = result.find(mc + "AlternateContent")
    choice = alternate.find(mc + "Choice")
    assert (
        alternate.nsmap[alternate.get(mc + "Ignorable")] == "http://schemas.microsoft.com/office/drawing/2007/8/2/chart"
    )
    assert choice.nsmap[choice.get("Requires")] == "http://schemas.microsoft.com/office/drawing/2007/8/2/chart"


def test_literal_numeric_percentage_format_survives_data_replacement():
    root = ET.fromstring(chart_xml())
    literal = ET.SubElement(root.find(f".//{C}ser/{C}val"), C + "numLit")
    ET.SubElement(literal, C + "formatCode").text = "0.00%"
    parts = restore_chart_data(ET.tostring(root), source())
    output = ET.fromstring(parts.chart_xml)
    assert output.findtext(f".//{C}val/{C}numRef/{C}numCache/{C}formatCode") == "0.00%"
    with ZipFile(BytesIO(parts.workbook)) as package:
        styles = ET.fromstring(package.read("xl/styles.xml"))
        sheet = ET.fromstring(package.read("xl/worksheets/sheet1.xml"))
        cell = next(cell for cell in sheet.iter(S + "c") if cell.get("r") == "B3")
        xf = styles.find(S + "cellXfs")[int(cell.get("s"))]
        number_format = next(node for node in styles.iter(S + "numFmt") if node.get("numFmtId") == xf.get("numFmtId"))
        assert number_format.get("formatCode") == "0.00%"


@pytest.mark.parametrize("template_id", ["midnight-analytics", "noir-gold", "cyber-neon", "executive-ivory"])
def test_explicit_template_text_color_fixes_axes_without_changing_series_style(template_id):
    template = get_builtin_template(template_id, 1)
    root = etree.fromstring(chart_xml())
    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    plot = root.find(f"{C}chart/{C}plotArea")
    axis = etree.SubElement(plot, C + "valAx")
    text = etree.SubElement(axis, C + "txPr")
    etree.SubElement(text, a + "bodyPr")
    etree.SubElement(text, a + "lstStyle")
    paragraph = etree.SubElement(text, a + "p")
    props = etree.SubElement(etree.SubElement(paragraph, a + "pPr"), a + "defRPr", sz="900", b="1")
    etree.SubElement(etree.SubElement(props, a + "solidFill"), a + "srgbClr", val="000000")
    etree.SubElement(props, a + "latin", typeface="Arial")
    etree.SubElement(axis, C + "crossAx", val="2")
    category = etree.SubElement(plot, C + "catAx")
    etree.SubElement(category, C + "crossAx", val="1")
    before_style = etree.tostring(root.find(f".//{C}ser/{C}spPr"))

    result = etree.fromstring(restore_chart_data(etree.tostring(root), source(), template=template).chart_xml)
    restored = result.find(f".//{C}valAx/{C}txPr/{a}p/{a}pPr/{a}defRPr")
    assert restored.attrib == {"sz": "900", "b": "1"}
    assert restored.find(a + "latin").get("typeface") == "Arial"
    assert restored.find(f"{a}solidFill/{a}srgbClr").get("val") == template.theme.text
    category = result.find(f".//{C}catAx")
    assert [child.tag for child in category] == [C + "txPr", C + "crossAx"]
    assert category.find(f"{C}txPr/{a}p/{a}pPr/{a}defRPr/{a}solidFill/{a}srgbClr").get("val") == template.theme.text
    assert etree.tostring(result.find(f".//{C}ser/{C}spPr")) == before_style
