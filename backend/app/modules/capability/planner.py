# -*- coding: utf-8 -*-
"""Intent classification + robust JSON extraction helper.

中文补充（能力构建器的模型侧小工具）：
- ``classify_intent`` 用一次低温度模型调用判断用户想创建/修改哪类能力对象；
- ``extract_json`` 是"从模型回复里抠 JSON 对象"的稳健解析器，被 capability 与
  provider 层的参数编译器共同依赖。
- 本模块不写库、不做鉴权，纯函数式；解析失败只抛 ValueError，由调用方决定兜底策略。
"""
from __future__ import annotations

import json

from ...providers.model import chat_for_run

_CLASSIFY_SYSTEM = """判断用户意图要创建/修改哪种企业 AI 能力对象，只输出 JSON：
{"object": "skill|connector|plugin|resource_binding|output|other", "reason": "简短原因"}

规则：
- “以后分析Excel都按这个方法/把这个做法保存下来/以后技术方案重点强调实施计划” -> skill
- “接入这个API/接一下这个PPT接口/配置这个MCP” -> connector
- “把分析方法和XX接口组合成一个能力/做成一个插件” -> plugin
- “数据以后改成从ERP/正式数据库来/不要用Excel改查数据库” -> resource_binding
- “以后同时生成PPT和Word/输出改成PPT” -> output
"""


def classify_intent(text: str, *, workspace_id: str | None = None) -> dict:
    """把用户输入归类为 skill / connector / plugin / resource_binding / output / other。

    参数：``workspace_id`` 透传给模型调用用于计量与路由。
    返回：{"object","reason"}；模型输出无法解析时回落到 {"object":"other"}，
    并把原文前 200 字作为 reason，保证调用方永远拿到可用结构（不做异常穿透）。
    副作用：调用一次模型服务（temperature=0，尽量确定性）。
    """
    resp = chat_for_run("reasoning", [
        {"role": "system", "content": _CLASSIFY_SYSTEM},
        {"role": "user", "content": text},
    ], temperature=0.0, workspace_id=workspace_id)
    try:
        plan = extract_json(resp.get("content") or "")
        obj = plan.get("object") or "other"
        return {"object": obj, "reason": plan.get("reason", "")}
    except Exception:
        return {"object": "other", "reason": resp.get("content", "")[:200]}


def extract_json(text: str) -> dict:
    """从模型回复中提取第一个完整的 JSON 对象（剥离 ```json 代码围栏）。

    解析顺序：去掉首尾代码围栏 → 整体 json.loads → 否则按花括号配对扫描，
    且只在**字符串字面量之外**统计深度（模型常在解释文字里写 "}"）。
    只接受 JSON（不用 eval/ast.literal_eval），因此不会执行模型产出的任何代码。
    返回 dict；完全解析不出对象时抛 ValueError("JSON 不完整")。
    """
    text = (text or "").strip()
    lines = text.split("\n")
    if lines and lines[0].strip().startswith("\u0060" * 3):
        lines = lines[1:]
    if lines and lines[-1].strip() == ("\u0060" * 3):
        lines = lines[:-1]
    text = "\n".join(lines)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    # Scan object boundaries while respecting JSON strings and escapes.  A
    # closing brace in a string (for example ``"rule: }"``) is data, not the
    # end of the model response.  Keeping this parser local avoids accepting
    # Python literals or executing model output.
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            current = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        return value
                    break
    raise ValueError("JSON 不完整")
