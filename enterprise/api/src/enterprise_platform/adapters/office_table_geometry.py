"""Compact a generated continuation table using explicit measured template row heights.

Only row heights and the matching graphic-frame height change. Content, fonts,
colors, column widths, position and surrounding decorations are retained. The
caller must measure text fit; this function neither clips nor paginates content.
"""

from lxml import etree

from enterprise_platform.application.errors import InvalidInput

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def compact_table_rows(slide_xml: bytes, *, shape_id: int, row_heights: tuple[int, ...]) -> bytes:
    if type(shape_id) is not int or shape_id < 1 or not row_heights:
        raise InvalidInput("office_table_geometry_invalid")
    if any(type(height) is not int or height < 1 for height in row_heights):
        raise InvalidInput("office_table_geometry_invalid")
    try:
        source = slide_xml.decode("utf-8")
        if "\x00" in source or "<!doctype" in source.lower() or "<!entity" in source.lower():
            raise InvalidInput("office_table_geometry_invalid")
        root = etree.fromstring(
            slide_xml, parser=etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
        )
    except (UnicodeDecodeError, etree.XMLSyntaxError):
        raise InvalidInput("office_table_geometry_invalid") from None
    matches = [node for node in root.iter(P + "cNvPr") if node.get("id") == str(shape_id)]
    if root.tag != P + "sld" or len(matches) != 1:
        raise InvalidInput("office_table_geometry_invalid")
    properties = matches[0].getparent()
    frame = properties.getparent() if properties is not None else None
    if frame is None or frame.tag != P + "graphicFrame":
        raise InvalidInput("office_table_geometry_invalid")
    rows = frame.findall(f"{A}graphic/{A}graphicData/{A}tbl/{A}tr")
    extent = frame.find(f"{P}xfrm/{A}ext")
    if len(rows) != len(row_heights) or extent is None:
        raise InvalidInput("office_table_geometry_invalid")
    try:
        available = int(extent.get("cy", ""))
    except ValueError:
        raise InvalidInput("office_table_geometry_invalid") from None
    height = sum(row_heights)
    if available < 1 or height > available:
        raise InvalidInput("office_table_geometry_invalid")
    for row, row_height in zip(rows, row_heights, strict=True):
        row.set("h", str(row_height))
    extent.set("cy", str(height))
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)
