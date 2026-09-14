"""Restore native chart data without changing template drawing/layout properties.

Chart caches and the accompanying editable XLSX store the same exact decimal
lexemes. Office applications may round numbers when calculating or resaving;
these bytes are not a guarantee of arbitrary-precision spreadsheet arithmetic.
The PPT package assembler must install both parts using the existing relationship.
An explicit template also restores axis/legend text color from its palette; omitting
it retains existing text styling for data-only repairs.
"""

from dataclasses import dataclass
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from enterprise_platform.application.errors import InvalidInput
from enterprise_platform.domain.office_content import ChartData
from enterprise_platform.domain.office_templates import OfficeTemplate

C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P = "{http://schemas.openxmlformats.org/package/2006/relationships}"
T = "{http://schemas.openxmlformats.org/package/2006/content-types}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


@dataclass(frozen=True, slots=True)
class OfficeChartParts:
    chart_xml: bytes
    workbook: bytes


def _column(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _text(value: str) -> str:
    if any(
        not (
            ord(char) in {9, 10, 13}
            or 0x20 <= ord(char) <= 0xD7FF
            or 0xE000 <= ord(char) <= 0xFFFD
            or 0x10000 <= ord(char) <= 0x10FFFF
        )
        for char in value
    ):
        raise InvalidInput("office_chart_text_invalid")
    return value


def _xml(root: ET.Element) -> bytes:
    value = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if not isinstance(value, bytes):
        raise InvalidInput("office_chart_structure_invalid")
    return value


def _string_cell(row: ET.Element, address: str, value: str) -> None:
    cell = ET.SubElement(row, S + "c", r=address, t="inlineStr")
    text = ET.SubElement(ET.SubElement(cell, S + "is"), S + "t")
    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text.text = value


def _styles(formats: tuple[str, ...]) -> ET.Element:
    styles = ET.Element(S + "styleSheet")
    number_formats = ET.SubElement(styles, S + "numFmts", count=str(len(formats)))
    for index, code in enumerate(formats, 164):
        ET.SubElement(number_formats, S + "numFmt", numFmtId=str(index), formatCode=code)
    font = ET.SubElement(ET.SubElement(styles, S + "fonts", count="1"), S + "font")
    ET.SubElement(font, S + "sz", val="11")
    ET.SubElement(font, S + "name", val="Calibri")
    fills = ET.SubElement(styles, S + "fills", count="2")
    for pattern in ("none", "gray125"):
        ET.SubElement(ET.SubElement(fills, S + "fill"), S + "patternFill", patternType=pattern)
    ET.SubElement(ET.SubElement(styles, S + "borders", count="1"), S + "border")
    attributes = {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"}
    ET.SubElement(ET.SubElement(styles, S + "cellStyleXfs", count="1"), S + "xf", attributes)
    xfs = ET.SubElement(styles, S + "cellXfs", count=str(len(formats) + 1))
    ET.SubElement(xfs, S + "xf", {**attributes, "xfId": "0"})
    for index in range(len(formats)):
        ET.SubElement(
            xfs, S + "xf", {**attributes, "numFmtId": str(index + 164), "xfId": "0", "applyNumberFormat": "1"}
        )
    ET.SubElement(
        ET.SubElement(styles, S + "cellStyles", count="1"), S + "cellStyle", name="Normal", xfId="0", builtinId="0"
    )
    return styles


def _workbook(data: ChartData, formats: tuple[str, ...]) -> bytes:
    sheet = ET.Element(S + "worksheet")
    rows = ET.SubElement(sheet, S + "sheetData")
    header = ET.SubElement(rows, S + "row", r="1")
    _string_cell(header, "A1", "Category")
    for index, series in enumerate(data.series, 2):
        _string_cell(header, f"{_column(index)}1", series.name)
    for index, category in enumerate(data.categories, 2):
        row = ET.SubElement(rows, S + "row", r=str(index))
        _string_cell(row, f"A{index}", category)
        for column, series in enumerate(data.series, 2):
            value = series.values[index - 2]
            if value is not None:
                cell = ET.SubElement(row, S + "c", r=f"{_column(column)}{index}", t="n", s=str(column - 1))
                ET.SubElement(cell, S + "v").text = str(value)
    book = ET.Element(S + "workbook")
    ET.SubElement(ET.SubElement(book, S + "sheets"), S + "sheet", {"name": "Sheet1", "sheetId": "1", R + "id": "rId1"})
    root_rels = ET.Element(P + "Relationships")
    ET.SubElement(root_rels, P + "Relationship", Id="rId1", Type=R[1:-1] + "/officeDocument", Target="xl/workbook.xml")
    book_rels = ET.Element(P + "Relationships")
    ET.SubElement(book_rels, P + "Relationship", Id="rId1", Type=R[1:-1] + "/worksheet", Target="worksheets/sheet1.xml")
    ET.SubElement(book_rels, P + "Relationship", Id="rId2", Type=R[1:-1] + "/styles", Target="styles.xml")
    types = ET.Element(T + "Types")
    ET.SubElement(
        types, T + "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml"
    )
    ET.SubElement(types, T + "Default", Extension="xml", ContentType="application/xml")
    for name, kind in (
        ("/xl/workbook.xml", "sheet.main"),
        ("/xl/worksheets/sheet1.xml", "worksheet"),
        ("/xl/styles.xml", "styles"),
    ):
        ET.SubElement(
            types,
            T + "Override",
            PartName=name,
            ContentType=f"application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml",
        )
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as package:
        for name, root in (
            ("[Content_Types].xml", types),
            ("_rels/.rels", root_rels),
            ("xl/workbook.xml", book),
            ("xl/_rels/workbook.xml.rels", book_rels),
            ("xl/worksheets/sheet1.xml", sheet),
            ("xl/styles.xml", _styles(formats)),
        ):
            package.writestr(name, _xml(root))
    return output.getvalue()


def _string_reference(parent: etree._Element, formula: str, values: tuple[str, ...]) -> None:
    parent.clear()
    reference = etree.SubElement(parent, C + "strRef")
    etree.SubElement(reference, C + "f").text = formula
    cache = etree.SubElement(reference, C + "strCache")
    etree.SubElement(cache, C + "ptCount", val=str(len(values)))
    for index, value in enumerate(values):
        etree.SubElement(etree.SubElement(cache, C + "pt", idx=str(index)), C + "v").text = value


def _text_fill(properties: etree._Element, color: str) -> None:
    fills = {A + name for name in ("noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill")}
    existing = [child for child in properties if child.tag in fills]
    position = properties.index(existing[0]) if existing else (1 if properties.find(A + "ln") is not None else 0)
    for child in existing:
        properties.remove(child)
    fill = etree.Element(A + "solidFill")
    etree.SubElement(fill, A + "srgbClr", val=color)
    properties.insert(position, fill)


def _template_axis_text(root: etree._Element, template: OfficeTemplate) -> None:
    containers = {C + name for name in ("catAx", "valAx", "dateAx", "serAx", "legend", "legendEntry")}
    for container in root.iter():
        if container.tag not in containers:
            continue
        text = container.find(C + "txPr")
        if text is None:
            text = etree.Element(C + "txPr")
            position = next(
                (i for i, child in enumerate(container) if child.tag in {C + "crossAx", C + "extLst"}), len(container)
            )
            container.insert(position, text)
            etree.SubElement(text, A + "bodyPr")
            etree.SubElement(text, A + "lstStyle")
        paragraphs = text.findall(A + "p")
        if not paragraphs:
            paragraphs = [etree.SubElement(text, A + "p")]
        for paragraph in paragraphs:
            properties = paragraph.find(A + "pPr")
            if properties is None:
                properties = etree.Element(A + "pPr")
                paragraph.insert(0, properties)
            default = properties.find(A + "defRPr")
            if default is None:
                default = etree.Element(A + "defRPr")
                extension = properties.find(A + "extLst")
                properties.insert(properties.index(extension) if extension is not None else len(properties), default)
        for properties in text.iter():
            if properties.tag in {A + "defRPr", A + "rPr", A + "endParaRPr"}:
                _text_fill(properties, template.theme.text)


def restore_chart_data(
    chart_xml: bytes, data: ChartData, *, template: OfficeTemplate | None = None
) -> OfficeChartParts:
    """Repair a generated category chart; reject incompatible series mapping."""
    try:
        source = chart_xml.decode("utf-8")
        if "\x00" in source or "<!doctype" in source.lower() or "<!entity" in source.lower():
            raise InvalidInput("office_chart_structure_invalid")
        root = etree.fromstring(
            chart_xml, parser=etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
        )
    except (etree.XMLSyntaxError, UnicodeDecodeError):
        raise InvalidInput("office_chart_structure_invalid") from None
    chart = root.find(C + "chart")
    nodes = root.findall(f"{C}chart/{C}plotArea/*/{C}ser")
    if root.tag != C + "chartSpace" or chart is None or len(nodes) != len(data.series):
        raise InvalidInput("office_chart_structure_invalid")
    for label in (*data.categories, *(series.name for series in data.series)):
        _text(label)
    formats: list[str] = []
    for index, (node, series) in enumerate(zip(nodes, data.series, strict=True)):
        title, categories, values = (node.find(C + name) for name in ("tx", "cat", "val"))
        order = node.find(C + "order")
        identity = node.find(C + "idx")
        if (
            title is None
            or categories is None
            or values is None
            or order is None
            or identity is None
            or order.get("val") != str(index)
            or identity.get("val") != str(index)
        ):
            raise InvalidInput("office_chart_structure_invalid")
        column, end = _column(index + 2), len(data.categories) + 1
        _string_reference(title, f"Sheet1!${column}$1", (series.name,))
        _string_reference(categories, f"Sheet1!$A$2:$A${end}", data.categories)
        number_format = values.findtext(f"{C}numRef/{C}numCache/{C}formatCode")
        if number_format is None:
            number_format = values.findtext(f"{C}numLit/{C}formatCode", default="General")
        formats.append(number_format)
        values.clear()
        reference = etree.SubElement(values, C + "numRef")
        etree.SubElement(reference, C + "f").text = f"Sheet1!${column}$2:${column}${end}"
        cache = etree.SubElement(reference, C + "numCache")
        etree.SubElement(cache, C + "formatCode").text = number_format
        etree.SubElement(cache, C + "ptCount", val=str(len(series.values)))
        for point_index, value in enumerate(series.values):
            if value is not None:
                etree.SubElement(etree.SubElement(cache, C + "pt", idx=str(point_index)), C + "v").text = str(value)
    blanks = chart.find(C + "dispBlanksAs")
    if blanks is None:
        blanks = etree.Element(C + "dispBlanksAs")
        # Keep schema order: dispBlanksAs precedes showDLblsOverMax and extLst.
        insertion = next(
            (i for i, child in enumerate(chart) if child.tag in {C + "showDLblsOverMax", C + "extLst"}), len(chart)
        )
        chart.insert(insertion, blanks)
    blanks.set("val", "gap")
    if template is not None:
        _template_axis_text(root, template)
    serialized = etree.tostring(root, encoding="utf-8", xml_declaration=True)
    return OfficeChartParts(chart_xml=serialized, workbook=_workbook(data, tuple(formats)))
