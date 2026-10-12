"""Explicit saved POST placeholders; runtime values cannot change targets or credentials."""
from __future__ import annotations

import json
import re

from app.errors import ValidationError

INPUT_REFERENCE = re.compile(r"\$\{input\.([A-Za-z][A-Za-z0-9]*)\}")
INPUT_FIELDS = frozenset({"sampleId", "imageBase64", "imageMimeType", "imageSha256", "imageWidth", "imageHeight", "imageOrientation"})
MAX_DYNAMIC_BODY_BYTES = 8 * 1024 * 1024
MAX_DYNAMIC_BATCH_BYTES = 16 * 1024 * 1024


def parameter_names(value) -> set[str]:
    """Only a complete JSON value may reference a finite input field."""
    if isinstance(value, str) and "${input." in value:
        match = INPUT_REFERENCE.fullmatch(value)
        if not match or match.group(1) not in INPUT_FIELDS:
            raise ValidationError("动态参数必须完整使用 ${input.字段名}；只允许样本编号和已授权图片参数")
        return {match.group(1)}
    if isinstance(value, dict):
        if any("${input." in key for key in value):
            raise ValidationError("动态参数只能替换 JSON 值，不能替换字段名")
        return set().union(*(parameter_names(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(parameter_names(item) for item in value))
    return set()


def bind_parameters(config, parameters: list[dict] | None):
    """Preflight every body under a batch budget before the first HTTP request."""
    names = parameter_names(config.bodies)
    if parameters is None:
        if names:
            raise ValidationError("此请求需要动态样本参数，请从质检工作流的输入模式执行")
        return config
    if config.method != "POST" or len(config.bodies) != 1 or not names:
        raise ValidationError("动态检测需配置一条带输入占位符的 POST 请求模板")
    if not 1 <= len(parameters) <= 100:
        raise ValidationError("单次动态检测需提供 1–100 个不同样本")
    bodies, total = [], 0
    for values in parameters:
        if names - values.keys() or values.keys() - INPUT_FIELDS:
            raise ValidationError("缺少模板所需样本/图片参数，或包含未允许的参数")
        for value in values.values():
            if not isinstance(value, (str, int)) or isinstance(value, bool) or (isinstance(value, str) and "${" in value):
                raise ValidationError("动态值必须是普通文本或整数，不能注入其他输入或凭据引用")
        used = 0
        def charge(value) -> None:
            """累计本条请求体已占用的字节；单条超 8 MB 或整批超 16 MB 立即拒绝。

            必须在构造返回值之前记账，否则多张图片展开后才会发现超限，白占用内存。
            """
            nonlocal used
            used += len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if used > MAX_DYNAMIC_BODY_BYTES or total + used > MAX_DYNAMIC_BATCH_BYTES:
                raise ValidationError("动态请求展开后单条最多 8 MB、整批最多 16 MB；请减少样本或图片大小")
        def replace(value):
            """把 ${input.字段名} 替换为服务端校验过的样本值，并在替换前后记账。

            占位符必须**整体**占用一个 JSON 值（不能出现在键名或字符串片段里），
            因此运行期不可能借此改写目标地址、请求头或凭据引用。
            """
            nonlocal used
            if isinstance(value, str) and (match := INPUT_REFERENCE.fullmatch(value)):
                selected = values[match.group(1)]
                charge(selected)
                return selected
            if isinstance(value, dict):
                # Account for braces, separators and quoted keys before traversing.
                # Never serialize an unbounded expansion of repeated large images.
                used += 2 + max(0, len(value) - 1) * 2 + len(value) * 2
                for key in value:
                    charge(key)
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                used += 2 + max(0, len(value) - 1) * 2
                return [replace(item) for item in value]
            charge(value)
            return value
        body = replace(config.bodies[0])
        size = len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        total += size
        if size > MAX_DYNAMIC_BODY_BYTES or total > MAX_DYNAMIC_BATCH_BYTES:
            raise ValidationError("动态请求展开后单条最多 8 MB、整批最多 16 MB；请减少样本或图片大小")
        bodies.append(body)
    # Saved schema remains limited to 1 MB; only validated in-memory expansions
    # can use the larger image budget, never modifying the persisted source.
    return config.model_copy(update={"bodies": bodies}, deep=True)
