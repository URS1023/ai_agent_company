"""精提Test5边框SVG和装饰分支；同源类型按原option变体输出。"""
import html
from pathlib import Path
import re


def _embedded(value):
    """只允许内部生成或用户自带的有限位图data URI，不能夹带HTML属性或脚本。"""
    return value if isinstance(value, str) and len(value) <= 3_000_000 and re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+", value) else ""


def material_styles():
    return (Path(__file__).parent / "resources" / "material_styles.css").read_text(encoding="utf-8")


def render_material(widget, rows, asset, css):
    component, config = widget.get("component"), widget.get("config", {})
    option = config.get("option", {})
    main = css(option.get("mainColor"), "#05f8d6")
    sub = css(option.get("subColor"), "#0082fc")
    style = f"--main-color:{main};--sub-color:{sub};"
    try:
        variant = str(int(option.get("type", 7 if component == "JDragBorder" else 5)))
    except (ValueError, TypeError):
        variant = "7" if component == "JDragBorder" else "5"
    width, height = max(30, float(widget.get("w", 760))), max(30, float(widget.get("h", 420)))
    if component == "JDragDecoration":
        return f'<div class="jimu-decoration decoration-{variant}" style="{style}"><span></span><i></i><span></span></div>'
    if component == "JDragBorder":
        shapes = []
        if variant == "5":
            points = [f"8,5 {width-5},5 {width-5},{height-100} {width-100},{height-5} 8,{height-5} 8,5",
                      f"3,0 {width},0 {width},{height-78} {width-74},{height-5} 3,{height-5} 3,5",
                      f"50,13 {width-35},13", f"15,20 {width-35},20",
                      f"15,{height-20} {width-110},{height-20}", f"15,{height-13} {width-110},{height-13}"]
            shapes = [f'<polyline class="b5-line-{i+1}" points="{value}"/>' for i, value in enumerate(points)]
        elif variant == "12":
            shapes = [f'<rect x="2" y="2" width="{width-4}" height="{height-4}" rx="12"/>', f'<rect class="runner" x="9" y="9" width="{width-18}" height="{height-18}" rx="8"/>']
        else:
            if variant not in ("1", "4", "6", "9"):
                shapes.append(f'<rect class="border-base" x="3" y="3" width="{width-6}" height="{height-6}"/>')
            if variant in ("3", "7", "10", "11"):
                shapes.append(f'<rect class="border-inner" x="10" y="10" width="{width-20}" height="{height-20}"/>')
            if variant == "4":
                shapes.append(f'<polygon class="border-base" points="18,3 {width-18},3 {width-3},18 {width-3},{height-18} {width-18},{height-3} 18,{height-3} 3,{height-18} 3,18"/>')
            if variant in ("1", "9"):
                for points in ("3,32 3,3 32,3", f"{width-32},3 {width-3},3 {width-3},32", f"{width-32},{height-3} {width-3},{height-3} {width-3},{height-32}", f"3,{height-32} 3,{height-3} 32,{height-3}"):
                    shapes.append(f'<polyline points="{points}"/>')
            if variant == "6":
                shapes.extend([f'<polyline points="3,70 3,3 95,3"/>', f'<polyline points="{width-95},{height-3} {width-3},{height-3} {width-3},{height-70}"/>', f'<line x1="18" y1="13" x2="{width-45}" y2="13"/>'])
            if variant == "11":
                shapes.append(f'<path class="border-tab" d="M{width*.35},10 L{width*.41},2 H{width*.59} L{width*.65},10"/>')
        generic = "" if variant in ("5", "12") else " border-generic"
        return f'<svg class="jimu-border border-{variant}{generic}" style="{style}" viewBox="0 0 {width} {height}" preserveAspectRatio="none">' + ''.join(shapes) + '</svg>'
    if component in ("JImg", "JCustomIcon", "JDragImg"):
        path = config.get("imageUrl", config.get("src", config.get("url", widget.get("image", ""))))
        image = _embedded(widget.get("_export_image_uri")) or asset(path)
        if image:
            fit = config.get("objectFit", option.get("fit", "contain"))
            fit = fit if fit in ("contain", "cover", "fill", "none", "scale-down") else "contain"
            return f'<img src="{image}" alt="{html.escape(str(widget.get("componentName", "")),quote=True)}" style="width:100%;height:100%;object-fit:{fit}">'
        if component == "JCustomIcon":
            name, color = option.get("icon", "fa-solid fa-star"), css(option.get("iconColor", option.get("color")), main)
            paths = {"star": "M50 5 L62 36 L95 38 L69 60 L77 94 L50 75 L23 94 L31 60 L5 38 L38 36 Z", "heart": "M50 88 C-8 52 8 7 34 22 L50 35 L66 22 C92 7 108 52 50 88 Z", "check": "M10 50 L36 75 L90 18 L98 27 L36 94 L2 59 Z", "warning": "M50 4 L98 94 H2 Z M45 32 V65 H55 V32 Z M45 76 V85 H55 V76 Z", "gear": "M36 10 H64 L68 24 L82 30 L94 42 V58 L82 70 L68 76 L64 90 H36 L32 76 L18 70 L6 58 V42 L18 30 L32 24 Z M50 32 A18 18 0 1 0 50 68 A18 18 0 1 0 50 32 Z"}
            paths.update({"fa-solid fa-star": paths["star"], "fa-solid fa-gears": paths["gear"],
                          "fa-solid fa-industry": "M6 40 L32 25 V42 L60 25 V45 H76 V10 H89 V45 H96 V92 H6 Z M16 60 H29 V72 H16 Z M40 60 H53 V72 H40 Z M64 60 H77 V72 H64 Z",
                          "fa-solid fa-shield-halved": "M50 3 L92 20 V53 C92 74 73 91 50 99 C27 91 8 74 8 53 V20 Z M50 16 V85 C68 77 80 65 80 51 V28 Z",
                          "fa-solid fa-chart-line": "M8 8 H18 V80 H94 V92 H8 Z M24 61 L43 40 L60 52 L86 19 L96 27 L61 69 L44 57 L33 70 Z",
                          "fa-solid fa-clipboard-check": "M32 5 H68 V14 H86 V95 H14 V14 H32 Z M38 12 V24 H62 V12 Z M26 58 L43 75 L76 40 L67 31 L43 57 L35 49 Z"})
            path = paths.get(name, paths["star"]) if isinstance(name, str) else paths["star"]
            return '<svg viewBox="0 0 100 100" role="img" aria-label="图标"><path fill-rule="evenodd" fill="' + color + '" d="' + path + '"/></svg>'
        return '<div class="text" aria-label="未选择图片">' + html.escape(str(widget.get("componentName", "图片"))) + '</div>'
    if component == "JCarousel":
        slides = []
        images = config.get("images", [])
        source_rows = images if isinstance(images, list) and images else rows
        for row in source_rows[:20]:
            if isinstance(row, str):
                row = {"url": row}
            if not isinstance(row, dict):
                continue
            path = row.get("url", row.get("src", row.get("imageUrl", "")))
            image = _embedded(row.get("_export_image_uri")) or asset(path)
            if not image:
                continue
            label = html.escape(str(row.get("name", row.get("title", ""))))
            slides.append(f'<figure><img src="{image}" alt="{label}"><figcaption>{label}</figcaption></figure>')
        return '<div class="material-carousel-static" style="display:flex;width:100%;height:100%;overflow:hidden">' + ''.join(slides[:1]) + '</div>' if slides else '<div class="text">暂无轮播图片</div>'
    return None
