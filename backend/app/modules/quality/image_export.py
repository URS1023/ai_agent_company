"""Self-contained quality image export, preserving original bytes and defect locations.

职责：把质检报告里的原图与缺陷框导出为**自包含 HTML 片段**（图片以 base64 内嵌，
缺陷框用百分比定位叠加），可直接嵌入 Word/网页报告。

位置与依赖：被质检报告导出流程调用；图片读取经 ``quality.images.image_content``，
因此仍受图片授权与内容校验约束（不会绕过权限读文件）。

约束：
- 只处理带 ``provenance.image`` 快照的项，其余项跳过（不是失败）；
- 原图合计超过 16 MB 直接报错，而不是截断或降质 —— 提示用户从逐项预览查看原图；
- 缺陷框坐标为归一化值（相对 EXIF 纠正后的显示方向），乘 100 转百分比；
- 不发起任何外部请求；所有插入 HTML 的文本都经 ``escape``（含属性用 ``quote=True``），
  新增字段时必须保持同样处理，否则会引入 XSS。
"""
from base64 import b64encode
from html import escape

from app.common.deps import AuthContext
from app.errors import ValidationError
from app.modules.quality.images import image_content


def export_images(ctx: AuthContext, results: list[dict]) -> str:
    """Embed authorized original images and fixed annotations, without external requests.

    （中文补充）入参 ``results`` 是质检记录的逐项结果；对每项从
    ``provenance.image`` 取图片快照，读原图并生成带缺陷框的 ``<section>`` 片段，
    最后拼接返回（无图片的项返回空串，不是错误）。
    异常：累计原图体积超过 16 MB 时抛 ``ValidationError``（提示改用逐项预览）。
    副作用：仅读取图片内容，不写库、不落临时文件。
    """
    sections, size = [], 0
    for item in results:
        snapshot = item.get("provenance", {}).get("image")
        if not snapshot:
            continue
        size += snapshot["size"]
        if size > 16 * 1024 * 1024:
            raise ValidationError("报告原图合计超过 16 MB，请从逐项图片预览中查看原图")
        image, data = image_content(ctx, snapshot["id"], snapshot=snapshot, export=True)
        boxes = []
        for index, defect in enumerate((item.get("observation") or {}).get("defects") or []):
            if defect.get("bbox"):
                x, y, width, height = defect["bbox"]
                boxes.append(f'<span style="position:absolute;border:2px solid #c62828;box-sizing:border-box;left:{x*100}%;top:{y*100}%;width:{width*100}%;height:{height*100}%" title="{escape(defect["defectType"], quote=True)}">{index+1}</span>')
        sections.append(f'<section><h2>{escape(item["name"])} · 原图与缺陷位置</h2><p>{escape(image.name)} · SHA-256: {image.sha256}</p>'
            f'<div style="position:relative;max-width:900px"><img alt="{escape(image.name, quote=True)}" style="display:block;width:100%;height:auto" src="data:{image.mimeType};base64,{b64encode(data).decode()}"/>'
            + "".join(boxes) + '</div></section>')
    return "".join(sections)
