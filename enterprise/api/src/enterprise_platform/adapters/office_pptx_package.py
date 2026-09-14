"""Install exact chart data into generated PPTX packages without altering other parts.

Bindings come from the renderer's unit-to-chart map, never positional guessing.
This is an in-memory assembly step, not an upload sanitizer or file publisher.
Pagination, revision metadata and authorization remain the caller's responsibility.
"""

import posixpath
import re
from copy import copy
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from lxml import etree

from enterprise_platform.application.errors import InvalidInput
from enterprise_platform.domain.office_content import ChartData
from enterprise_platform.domain.office_templates import OfficeTemplate

from .office_chart_package import restore_chart_data

C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P = "{http://schemas.openxmlformats.org/package/2006/relationships}"
T = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_CHART = re.compile(r"ppt/charts/chart[1-9][0-9]*\.xml\Z")
_PACKAGE_LIMIT = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ChartBinding:
    part_name: str
    data: ChartData


def _invalid() -> InvalidInput:
    return InvalidInput("office_presentation_package_invalid")


def _parse(data: bytes) -> etree._Element:
    try:
        text = data.decode("utf-8")
        if "\x00" in text or "<!doctype" in text.lower() or "<!entity" in text.lower():
            raise _invalid()
        return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    except (UnicodeDecodeError, etree.XMLSyntaxError):
        raise _invalid() from None


def _workbook_target(package: ZipFile, chart_name: str, chart: bytes, names: set[str]) -> str:
    external = _parse(chart).findall(C + "externalData")
    if len(external) != 1 or not (relationship_id := external[0].get(R + "id")):
        raise _invalid()
    directory, filename = posixpath.split(chart_name)
    relations = _parse(package.read(f"{directory}/_rels/{filename}.rels"))
    matches = [item for item in relations.findall(P + "Relationship") if item.get("Id") == relationship_id]
    if len(matches) != 1:
        raise _invalid()
    relation = matches[0]
    target = relation.get("Target", "")
    uri = urlsplit(target)
    if (
        relation.get("TargetMode", "Internal") != "Internal"
        or relation.get("Type") != R[1:-1] + "/package"
        or not target
        or "\\" in target
        or "%" in target
        or uri.scheme
        or uri.netloc
        or uri.query
        or uri.fragment
    ):
        raise _invalid()
    resolved = posixpath.normpath(posixpath.join(directory, target))
    if not resolved.startswith("ppt/embeddings/") or not resolved.endswith(".xlsx") or resolved not in names:
        raise _invalid()
    return resolved


def assemble_chart_data(
    pptx: bytes, bindings: tuple[ChartBinding, ...], *, template: OfficeTemplate | None = None
) -> bytes:
    """Update every bound chart plus its internal workbook, returning only a complete ZIP."""
    if len(pptx) > _PACKAGE_LIMIT:
        raise _invalid()
    try:
        with ZipFile(BytesIO(pptx)) as package:
            infos = package.infolist()
            names = {info.filename for info in infos}
            if (
                len(names) != len(infos)
                or len(infos) > 100000
                or sum(info.file_size for info in infos) > _PACKAGE_LIMIT
                or any(info.flag_bits & 1 for info in infos)
                or any(name.startswith("_xmlsignatures/") for name in names)
            ):
                raise _invalid()
            content_types = _parse(package.read("[Content_Types].xml"))
            presentation_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
            if "ppt/presentation.xml" not in names or not any(
                item.get("PartName") == "/ppt/presentation.xml" and item.get("ContentType") == presentation_type
                for item in content_types.findall(T + "Override")
            ):
                raise _invalid()
            charts = {name for name in names if _CHART.fullmatch(name)}
            overrides = {
                item.get("PartName", "").removeprefix("/"): item.get("ContentType", "")
                for item in content_types.findall(T + "Override")
            }
            defaults = {
                item.get("Extension", "").lower(): item.get("ContentType", "")
                for item in content_types.findall(T + "Default")
            }
            if len(overrides) != len(content_types.findall(T + "Override")) or len(defaults) != len(
                content_types.findall(T + "Default")
            ):
                raise _invalid()
            declared_charts = {
                name
                for name in names | overrides.keys()
                if overrides.get(name, defaults.get(posixpath.splitext(name)[1].removeprefix(".").lower(), ""))
                == "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
            }
            if not declared_charts.issubset(names):
                raise _invalid()
            charts.update(declared_charts)
            bound_names = {binding.part_name for binding in bindings}
            if len(bound_names) != len(bindings) or bound_names != charts:
                raise _invalid()
            replacements: dict[str, bytes] = {}
            for binding in bindings:
                original = package.read(binding.part_name)
                workbook = _workbook_target(package, binding.part_name, original, names)
                if workbook in replacements:
                    # Two chart bindings must not overwrite a shared workbook independently.
                    raise _invalid()
                parts = restore_chart_data(original, binding.data, template=template)
                replacements[binding.part_name] = parts.chart_xml
                replacements[workbook] = parts.workbook
            size = sum(
                len(replacements.get(info.filename, b"")) if info.filename in replacements else info.file_size
                for info in infos
            )
            if size > _PACKAGE_LIMIT:
                raise _invalid()
            output = BytesIO()
            with ZipFile(output, "w", compression=ZIP_DEFLATED) as target:
                target.comment = package.comment
                for info in infos:
                    # writestr mutates ZipInfo offsets; preserve the source directory for subsequent reads.
                    value = (
                        replacements[info.filename] if info.filename in replacements else package.read(info.filename)
                    )
                    target.writestr(copy(info), value)
            return output.getvalue()
    except (BadZipFile, KeyError, ValueError, NotImplementedError):
        raise _invalid() from None
