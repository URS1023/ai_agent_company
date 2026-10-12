"""Bind workflow samples and authorized original images to a saved detector template.

职责：检测节点的"上游输入模式"支撑 —— 校验已保存的 HTTP 请求模板能否接受样本参数，
并把上游样本（编号 + 授权原图）绑定成逐条请求参数。

位置与依赖：被 ``quality.detection`` 调用；图片读取走 ``quality.images.image_content``
（因此仍受图片授权与哈希校验约束），参数名解析走 ``collection`` 的模板工具。

约束（**不得编造/外传**）：
- ``saved`` 模式要求请求体**不含**样本占位符（否则应切换为输入模式，不能半途替换）；
- 输入模式要求单条 POST 模板，外观必须引用 ``sampleId`` 与 ``imageBase64``，成分只允许 ``sampleId``；
- 服务已选择的 JSONPath 输出字段必须覆盖 ``sampleId`` 与 ``defects`` / ``components``，
  否则检测结果无法被解析；
- 样本编号必须唯一非空且 ≤ 160 字符；原图合计超过 11 MB 直接报错（**尚未发送任何请求**）；
- 图片 base64 **不写入任何轨迹/日志**，只出现在本次请求参数中。
"""
from base64 import b64encode

from app.common.deps import AuthContext
from app.errors import ValidationError
from app.modules.collection.parameters import parameter_names
from app.modules.collection.sources import source_config
from app.modules.quality.images import image_content
from app.modules.quality.schemas import DetectorConfig


def validate_input_template(kind: str, config: DetectorConfig, source) -> None:
    """Fail on invalid input contracts during workflow preflight, before upstream calls.

    （中文补充）发布/预检阶段校验"检测服务能否被本节点驱动"：
    ``saved`` 模式下请求模板必须**没有**样本占位符；输入模式必须是单条 POST 且引用
    ``sampleId``（外观另需 ``imageBase64``，成分不得携带图片参数）；
    已配置的 JSONPath 输出字段必须覆盖样本编号与检测结果字段。
    异常：任一不满足抛 ``ValidationError``（面向配置人员的中文说明）；无副作用。
    """
    saved = source_config(source)
    names = parameter_names(saved.bodies)
    if config.requestMode == "saved":
        if names:
            raise ValidationError("HTTP 请求含样本占位符，请将检测节点切换为上游输入模式")
        return
    required = {"sampleId", "imageBase64"} if kind == "qc-appearance" else {"sampleId"}
    if saved.method != "POST" or len(saved.bodies) != 1 or not required.issubset(names):
        raise ValidationError("上游输入模式需要单条 POST 模板并引用 sampleId；外观还需引用 imageBase64")
    if kind == "qc-component" and names - {"sampleId"}:
        raise ValidationError("成分节点仅发送样本参数；图片参数请使用外观检测节点")
    output_names = {field.name for field in saved.fields}
    required_outputs = {"sampleId", "defects" if kind == "qc-appearance" else "components"}
    if output_names and not required_outputs.issubset(output_names):
        raise ValidationError("检测服务已选择的 JSONPath 输出字段未覆盖样本编号及检测结果")


def detector_parameters(ctx: AuthContext, kind: str, config: DetectorConfig, value, budget) -> tuple[list[dict] | None, dict]:
    """Authorize all files before sending any batch item; never persist image base64 in traces.

    （中文补充）返回 ``(请求参数列表 或 None, {sampleId: 图片快照})``；
    ``saved`` 模式返回 ``(None, {})``，表示直接执行已保存请求体。
    输入模式逐个样本：校验编号唯一非空、取授权原图（外观）、累计体积（超 11 MB 即失败），
    并把图片元信息（类型/哈希/尺寸/EXIF 方向）一并放进参数，便于检测服务按正确方向解释缺陷框。
    异常：样本数不在 1–100、编号非法或重复、外观缺 ``imageFileId``、原图超限时抛 ``ValidationError``；
    整个函数在**发出任何外部请求之前**完成全部校验，失败时不会产生外部调用。
    """
    if config.requestMode == "saved":
        return None, {}
    from app.modules.workflows.nodes import records

    rows = records(value)
    if not 1 <= len(rows) <= 100:
        raise ValidationError("动态检测输入需要 1–100 条样本记录")
    parameters, evidence, total = [], {}, 0
    for row in rows:
        budget.consume()
        sample = row.get(config.sampleField)
        if not isinstance(sample, str) or not sample.strip() or len(sample) > 160 or sample in evidence:
            raise ValidationError("样本字段必须为唯一非空文本，且不超过 160 字符")
        values = {"sampleId": sample}
        snapshot = None
        if kind == "qc-appearance":
            file_id = row.get(config.imageField)
            if not isinstance(file_id, str) or not 1 <= len(file_id) <= 26:
                raise ValidationError("外观检测输入必须指定已授权的 imageFileId 图片字段")
            image, data = image_content(ctx, file_id, export=True)
            total += len(data)
            if total > 11 * 1024 * 1024:
                raise ValidationError("样本原图合计超限，请分批检测；尚未发送请求")
            snapshot = image.model_dump(mode="json")
            values.update(imageBase64=b64encode(data).decode(), imageMimeType=image.mimeType,
                          imageSha256=image.sha256, imageWidth=image.width, imageHeight=image.height,
                          imageOrientation=image.orientation)
        parameters.append(values)
        evidence[sample] = snapshot
    return parameters, evidence
