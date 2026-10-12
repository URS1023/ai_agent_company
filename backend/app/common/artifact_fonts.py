"""Server-owned Song font policy for generated office files and charts.

服务端自有的宋体策略：让生成的 office 文件与 matplotlib 图表在**任何客户端**打开都不掉字。

系统位置：产物落盘前由 ``modules/artifact/service.py``（``normalize_office_fonts``）统一字体；
沙箱执行前由 ``workers/sandbox_runner.py``（``install_chart_fonts``）把字体装进沙箱。

依赖与约束：只使用本机已安装的宋体（``simsun.ttc``）——**不联网下载字体，也不扫描用户目录**，
候选路径写死在 ``song_font_path``；字体缺失时抛 ``ValueError``（中文文案，可直接展示给用户）。
``normalize_office_fonts`` 是纯内存变换（不改内容、不写盘），对非 office 扩展名原样返回；
``install_chart_fonts`` 会全局打补丁 matplotlib（见其 docstring），只能在沙箱内一次性调用。
"""
from __future__ import annotations

import io
import os
from functools import wraps
from pathlib import Path
import re
from lxml import etree as ET
from zipfile import ZipFile

FONT_NAME = "宋体"
# office 包内的 XML 必须按命名空间限定名匹配：W=WordprocessingML，A=DrawingML，S=SpreadsheetML；
# 用无命名空间的 find("rPr") 会匹配不到，导致字体改写静默失效。
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def song_font_path() -> Path:
    """Use installed fonts only; never fetch fonts or scan user directories.

    返回本机可用的宋体文件路径（按固定候选顺序取第一个存在者：Windows Fonts → 两个常见 Linux 路径）。

    返回：``Path``。业务异常：三处候选都不存在时抛 ``ValueError``（中文提示"请安装宋体"），
    调用方应把它转成面向用户的错误而不是 500。副作用：无（只做 ``is_file()`` 探测，不复制、不下载）。
    """
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/simsun.ttc",
        Path("/usr/share/fonts/truetype/simsun.ttc"),
        Path("/usr/share/fonts/truetype/msttcorefonts/simsun.ttc"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise ValueError("缺少宋体字体文件，请安装宋体后重新生成中文图表或 PDF")


def normalize_office_fonts(data: bytes, extension: str) -> bytes:
    """Apply both Western/East Asian fonts, including styles and header/footer runs.

    把 office 包内所有字体引用统一改成宋体（含样式定义、页眉页脚与图表文本）。

    参数：``data`` 完整 office 包字节；``extension`` 带点扩展名（``.docx``/``.xlsx``/``.pptx``，
    大小写不敏感）。

    返回：重写字体后的完整包字节；扩展名不在三种之内时**原样返回** ``data``（幂等，可无条件调用）。

    副作用：无（纯内存 BytesIO ↔ ZipFile 变换，不写盘、不访问网络）。

    异常：包不是合法 zip 或 XML 无法解析时抛 ``zipfile.BadZipFile`` / ``lxml.etree.XMLSyntaxError``，
    由调用方决定是回退原文件还是报错。

    实现约束：XML 解析禁用外部实体与网络（``resolve_entities=False, no_network=True``，防 XXE）；
    zip 条目按原 ``ZipInfo`` 写回以保持压缩方式与顺序，避免破坏 office 对包结构的校验；
    只处理 ``word/`` ``xl/`` ``ppt/`` 下的 .xml，不碰媒体与其他部件。
    """
    extension = extension.lower()
    if extension not in {".docx", ".xlsx", ".pptx"}:
        return data
    output = io.BytesIO()
    with ZipFile(io.BytesIO(data)) as src, ZipFile(output, "w") as dst:
        for info in src.infolist():
            content = src.read(info.filename)
            if info.filename.endswith(".xml") and info.filename.startswith(("word/", "xl/", "ppt/")):
                element = ET.fromstring(content, parser=ET.XMLParser(resolve_entities=False, no_network=True))
                if extension == ".docx" and info.filename.startswith("word/"):
                    parents = list(element.iter(f"{{{W}}}r")) + list(element.iter(f"{{{W}}}style"))
                    for parent in parents:
                        rp = parent.find(f"{{{W}}}rPr")
                        if rp is None:
                            rp = ET.Element(f"{{{W}}}rPr")
                            if parent.tag == f"{{{W}}}r":
                                parent.insert(0, rp)
                            else:
                                # CT_Style places run properties after paragraph
                                # properties and before table properties.
                                later = {f"{{{W}}}{name}" for name in ("tblPr", "trPr", "tcPr", "tblStylePr")}
                                index = next((i for i, child in enumerate(parent) if child.tag in later), len(parent))
                                parent.insert(index, rp)
                        if parent.tag == f"{{{W}}}style":
                            # Repair earlier exports as well as fresh styles.
                            parent.remove(rp)
                            later = {f"{{{W}}}{name}" for name in ("tblPr", "trPr", "tcPr", "tblStylePr")}
                            index = next((i for i, child in enumerate(parent) if child.tag in later), len(parent))
                            parent.insert(index, rp)
                        fonts = rp.find(f"{{{W}}}rFonts")
                        if fonts is None:
                            fonts = ET.Element(f"{{{W}}}rFonts")
                        else:
                            rp.remove(fonts)
                        index = 1 if len(rp) and rp[0].tag == f"{{{W}}}rStyle" else 0
                        rp.insert(index, fonts)
                    for fonts in element.iter(f"{{{W}}}rFonts"):
                        for key in list(fonts.attrib):
                            if key.endswith("Theme"):
                                del fonts.attrib[key]
                        for key in ("ascii", "hAnsi", "eastAsia", "cs"):
                            fonts.set(f"{{{W}}}{key}", FONT_NAME)
                if extension == ".xlsx":
                    for font in list(element.iter(f"{{{S}}}font")) + list(element.iter(f"{{{S}}}rPr")):
                        tag = "name" if font.tag.endswith("}font") else "rFont"
                        name = font.find(f"{{{S}}}{tag}")
                        if name is None:
                            name = ET.SubElement(font, f"{{{S}}}{tag}")
                        name.set("val", FONT_NAME)
                        # Explicit Song must not be replaced by a workbook theme.
                        for scheme in list(font.findall(f"{{{S}}}scheme")):
                            font.remove(scheme)
                if extension == ".pptx":
                    # fld（页码/日期等字段）同样带 rPr，漏掉会在导出后回落到主题字体。
                    for run in list(element.iter(f"{{{A}}}r")) + list(element.iter(f"{{{A}}}fld")):
                        if run.find(f"{{{A}}}rPr") is None:
                            run.insert(0, ET.Element(f"{{{A}}}rPr"))
                    for tag in ("rPr", "defRPr", "endParaRPr", "majorFont", "minorFont"):
                        for rp in element.iter(f"{{{A}}}{tag}"):
                            for kind in ("latin", "ea", "cs"):
                                font = rp.find(f"{{{A}}}{kind}")
                                if font is None:
                                    font = ET.Element(f"{{{A}}}{kind}")
                                    later_names = {"latin": {"ea", "cs", "sym", "hlinkClick", "hlinkMouseOver", "rtl", "extLst", "font"},
                                                   "ea": {"cs", "sym", "hlinkClick", "hlinkMouseOver", "rtl", "extLst", "font"},
                                                   "cs": {"sym", "hlinkClick", "hlinkMouseOver", "rtl", "extLst", "font"}}[kind]
                                    later = {f"{{{A}}}{name}" for name in later_names}
                                    index = next((i for i, child in enumerate(rp) if child.tag in later), len(rp))
                                    rp.insert(index, font)
                                font.set("typeface", FONT_NAME)
                            if tag in {"majorFont", "minorFont"}:
                                # 主题字体必须一起改：未显式设置字体的 run 会回落到 major/minor 主题，
                                # 只改 run 级字体时中文仍可能显示成默认主题字体。
                                for font in rp.findall(f"{{{A}}}font"):
                                    font.set("typeface", FONT_NAME)
                content = ET.tostring(element, encoding="UTF-8", xml_declaration=True, standalone=True)
            dst.writestr(info, content)
    return output.getvalue()


def install_chart_fonts(sandbox_root: str) -> None:
    """Prepare a private font before sandbox lockdown; repair at text layout time.

    在沙箱内准备可用的中文字体与 matplotlib 环境（必须在沙箱进入只读/资源限制**之前**调用）。

    参数：``sandbox_root`` 沙箱工作目录，字体副本与 matplotlib 配置写入 ``<root>/.render_support/``。

    副作用（较多，均为进程级）：
    - 复制宋体到沙箱、设置 ``MPLCONFIGDIR``，并把 matplotlib 的字体表裁剪到
      "沙箱目录 + 解释器目录"内（沙箱内不能依赖系统字体目录）；
    - 全局替换 ``matplotlib.text.Text._get_layout``：含中文时改用私有宋体副本，
      若无对应字形则抛 ``ValueError``（避免生成"豆腐块"图）；
    - 全局替换 ``Figure.savefig``：中文刻度过多/过长时旋转并缩小字号后重排，保证图可读。

    异常：本机缺少宋体时经 ``song_font_path`` 抛 ``ValueError``；字形缺失在绘图阶段抛 ``ValueError``。
    注意：全局打补丁是刻意的（图表渲染分散在用户代码里），因此一个进程只应装一次、且只在沙箱进程内。
    """
    import shutil
    internal = Path(sandbox_root) / ".render_support"
    internal.mkdir(exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(internal / "matplotlib")
    path = internal / "simsun.ttc"
    shutil.copyfile(song_font_path(), path)
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager, ft2font
    from matplotlib.text import Text
    from matplotlib.figure import Figure
    import sys
    allowed_roots = [Path(sandbox_root).resolve(), Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()]
    font_manager.fontManager.ttflist = [entry for entry in font_manager.fontManager.ttflist
        if any(Path(entry.fname).resolve().is_relative_to(root) for root in allowed_roots)]
    font_manager.fontManager.addfont(str(path))
    # 必须清字体查找缓存：裁剪 ttflist 后旧缓存仍指向系统字体路径，在沙箱内不可读，
    # 表现为"字体已装好但渲染仍报找不到字体"。
    font_manager.fontManager._findfont_cached.cache_clear()
    name = font_manager.FontProperties(fname=str(path)).get_name()
    matplotlib.rcParams["font.family"] = [name]
    matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
    # 宋体没有合适的 Unicode 负号字形，保持默认会把负号渲染成方块。
    matplotlib.rcParams["axes.unicode_minus"] = False
    # 提前取字形表（charmap）供布局阶段快速判断缺字，避免逐字触发字体回退。
    coverage = ft2font.FT2Font(str(path)).get_charmap()
    layout = Text._get_layout

    @wraps(layout)
    def song_layout(text, renderer):
        """替换 ``Text._get_layout``：文本含中文时把字体换成沙箱内的宋体副本，缺字形则报错。

        只为含中文的文本切换字体（纯西文保留用户/样式选定字体），并保证不会静默产出"豆腐块"。
        异常：宋体缺少所需字形时抛 ``ValueError``（中文文案，可直接展示）。
        """
        chinese = re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text.get_text())
        if chinese:
            if any(ord(char) not in coverage for char in chinese):
                raise ValueError("宋体缺少图表所用的中文字形，请使用受支持的字体后导出")
            props = text.get_fontproperties().copy()
            props.set_file(str(path))
            text.set_fontproperties(props)
        return layout(text, renderer)

    Text._get_layout = song_layout
    save = Figure.savefig

    # pyplot 首次导入会检查 Figure.savefig 的所属类；保留原方法元信息，
    # 避免字体补丁导致所有首次绘图在导入阶段失败。
    @wraps(save)
    def save_readable(figure, *args, **kwargs):
        """替换 ``Figure.savefig``：中文刻度密集时先旋转/缩小再落盘，保证导出图可读。

        仅当某轴存在较多长中文刻度（>4 个刻度且其中有超过 5 个中文字符）且刻度未倾斜时才调整，
        避免影响用户显式设置过的旋转；调整后若从未设置布局引擎，用 ``tight_layout`` 重排。
        """
        changed = False
        for axes in figure.axes:
            labels = axes.get_xticklabels()
            if len(labels) > 4 and any(len(re.findall(r"[\u3400-\u9fff]", t.get_text())) > 5 for t in labels):
                for label in labels:
                    if abs(label.get_rotation()) <= 15:
                        label.set_rotation(35)
                        label.set_horizontalalignment("right")
                        label.set_fontsize(min(label.get_fontsize(), 9))
                        changed = True
        if changed and figure.get_layout_engine() is None:
            figure.tight_layout(pad=1.5)
        return save(figure, *args, **kwargs)

    Figure.savefig = save_readable
