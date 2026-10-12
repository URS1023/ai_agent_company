"""保留业务数值经过 JavaScript JSON 解码时的整数精度。"""
from __future__ import annotations

from typing import Any


JAVASCRIPT_SAFE_INTEGER = 2**53 - 1


def preserve_json_integers(value: Any) -> Any:
    """超出安全范围的整数用十进制文本传输，其他标量和原调用对象保持不变。

    字符串是业务原值，不尝试解码嵌套 JSON 或把数字字符串转回数值。
    布尔不是数量；列类型等业务元数据仍由原查询路径提供。
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value) if abs(value) > JAVASCRIPT_SAFE_INTEGER else value
    if isinstance(value, dict):
        return {key: preserve_json_integers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [preserve_json_integers(item) for item in value]
    return value
