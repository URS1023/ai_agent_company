"""报表传输的大整数规范；只处理真实 JSON 数值及组件约定的 chartData 字符串。"""
from __future__ import annotations

import json

from app.common.json_numbers import preserve_json_integers


def widget_for_wire(widget):
    """旧组件内的二次 JSON 编码也保持原整数；普通业务字符串不作解析。"""
    value = preserve_json_integers(widget)
    config = value.get("config") if isinstance(value, dict) else None
    if isinstance(config, dict) and isinstance(config.get("chartData"), str):
        try:
            decoded = json.loads(config["chartData"])
        except (ValueError, TypeError):
            return value
        normalized = preserve_json_integers(decoded)
        # 仅在数值编码确实变化时重写，保留普通素材的既有空白、键序和字符串格式。
        if isinstance(decoded, (dict, list)) and normalized != decoded:
            config["chartData"] = json.dumps(normalized, ensure_ascii=False)
    return value


def content_for_wire(content):
    """返回传输副本，不重写原始报表、查询快照或内容指纹。"""
    if isinstance(content, str):
        # 修订契约把 content 存成 JSON 字符串，需保护浏览器第二次解码。
        try:
            decoded = json.loads(content)
        except (ValueError, TypeError):
            return content
        if not isinstance(decoded, (dict, list)):
            return content
        normalized = content_for_wire(decoded)
        return json.dumps(normalized, ensure_ascii=False) if normalized != decoded else content
    value = preserve_json_integers(content)
    if isinstance(value, dict) and isinstance(value.get("widgets"), list):
        value["widgets"] = [widget_for_wire(widget) for widget in value["widgets"]]
    return value
