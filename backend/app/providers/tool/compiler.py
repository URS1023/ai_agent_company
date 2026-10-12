# -*- coding: utf-8 -*-
"""Parameter Compiler (sec 13)

Turns {User Intent + Conversation + Files + Knowledge + Tool Schema + Resource}
into validated ToolArguments JSON BEFORE execution. The agent loop runs tool
calls directly, but this explicit compiler is used for capability tests/plugins
and whenever a caller wants a deterministic "intent -> args" step.

Reference resolution: schema fields may declare x-lookup: <name> — a
registered resolver maps a natural-language value to the canonical id/value
(e.g. "北京XX项目" -> "P001823"). If no resolver resolves the field and the
schema marks it x-must-resolve: true, compilation fails instead of guessing.

中文补充：
- 定位：参数编译器是"可选的确定性前处理"，不在智能体主循环的工具调用路径上
  （主循环由模型直接产出参数并交给 validator 校验）。它用于能力测试、插件等
  需要"自然语言意图 → 规范化参数"的场景。
- 依赖：模型服务（providers.model.chat_for_run，profile 默认 reasoning）与
  validator.validate_arguments，因此编译结果与运行期校验规则完全一致。
- 安全/治理约束：x-lookup 字段的规范化只能由调用方提供的、已授权的引用数据完成
  （reference_resolver 不再访问任何数据库）；x-must-resolve 为真而无法解析时
  必须失败，禁止"猜一个值"。
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from ...errors import SchemaValidationError, ValidationError
from ...providers.model import chat_for_run
from .base import ToolSpec
from .validator import rule_validators, validate_arguments, validate_schema

logger = logging.getLogger("compiler")

_COMPILE_SYSTEM = """你负责把一个自然语言业务需求，编译成某个工具的 JSON 参数。
规则：
1. 严格按照 input_schema 生成参数：必填字段不许缺失，字段类型与枚举必须合法。
2. 从用户意图中提取实体值；意图没给出但有明确默认值时用默认值并说明。
3. 参数语义要准确：项目名、日期、金额等要匹配 schema 的 description 与 format（日期用 YYYY-MM-DD）。
4. 只输出 JSON 对象，禁止多余文字，禁止编造。
5. 若必要信息完全缺失，输出 {"_missing": ["字段名", ...], "reason": "说明"}。
"""


def _build_prompt(tool_spec: ToolSpec, intent: str, context: dict) -> str:
    """拼装编译提示词：工具说明 + input_schema + 用户需求 + 可选上下文。

    上下文三部分各有长度上限（会话 2000 字符、知识片段 8 条各 400 字符、
    引用提示全量），避免编译请求把模型上下文挤爆；引用提示只作为"可选对应"，
    最终是否采用仍由 x-lookup/x-must-resolve 决定。
    """
    lines = [
        f"目标工具：{tool_spec.name}",
        f"工具说明：{tool_spec.description}",
        "输入 Schema：",
        json.dumps(tool_spec.input_schema or {}, ensure_ascii=False, indent=1),
        "用户需求：",
        intent,
    ]
    if context.get("conversation"):
        lines.append("会话上下文：")
        lines.append(str(context["conversation"])[:2000])
    if context.get("knowledge"):
        lines.append("知识库/文件检索到的相关片段（可从中提取事实）：")
        for k in context["knowledge"][:8]:
            lines.append("- " + str(k)[:400])
    if context.get("references"):
        lines.append("资源提示（自然语言值到规范ID的可选对应）：")
        for k, v in context["references"].items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """从模型回复里提取 JSON 对象，容忍前后夹带的解释文字。

    解析策略：先整体 json.loads；失败后按花括号配对扫描，且只在**字符串字面量
    之外**计数深度（模型常在解释文本里写 {"a": "}"} 这类内容，简单计数会切错）。
    返回首个能解析成 dict 的片段；完全提不出对象时抛 ValidationError。
    """
    text = (text or "").strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    # Model explanations may contain braces inside quoted values.  Only
    # braces outside JSON strings contribute to object depth.
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
    raise ValidationError("参数编译 JSON 不完整")


def compile_arguments(
    tool_spec: ToolSpec,
    intent: str,
    *,
    context: Optional[dict] = None,
    resolver: Optional[Callable[[str, str], Optional[str]]] = None,
    max_retries: int = 2,
    profile: str = "reasoning",
    workspace_id: Optional[str] = None,
) -> dict:
    """从自然语言意图编译参数，经引用解析和完整契约校验后返回。

    参数说明：
    - ``intent``：用户自然语言需求；``context`` 可含 conversation / knowledge /
      references（供模型与解析器使用）。
    - ``resolver(lookup_name, raw_value)`` 返回规范值或 None；None 且 schema 标注
      x-must-resolve 时判定为失败。
    - ``max_retries`` 是"因参数校验失败而重试"的次数（默认 2），每次都会把上一次的
      校验错误追加到系统提示里；模型请求本身不在此重试。
    - ``workspace_id`` 透传给模型调用，用于计量与模型路由。
    返回：通过 validate_arguments 校验的参数 dict。
    异常：模型回复缺少必要信息（_missing）、必须解析却解析不到、多次重试仍不通过，
    统一抛 ValidationError（带中文原因）。
    """
    ctx = dict(context or {})
    # 已保存的旧契约也必须先校验，不能先调用模型后才发现约束本身不受支持。
    validate_schema(tool_spec.input_schema or {}, rule_validators)
    prompt = _build_prompt(tool_spec, intent, ctx)
    last_error: Optional[str] = None
    for attempt in range(max_retries + 1):
        sys = _COMPILE_SYSTEM
        if last_error:
            sys += "\n注意：上一次参数校验失败，务必修正：\n" + last_error
        resp = chat_for_run(profile, [
            {"role": "system", "content": sys},
            {"role": "user", "content": prompt},
        ], temperature=0.0, workspace_id=workspace_id)
        raw = _extract_json(resp.get("content") or "")
        if "_missing" in raw:
            raise ValidationError("参数编译缺少必要信息: %s (%s)" % (raw["_missing"], raw.get("reason", "")))
        props = (tool_spec.input_schema or {}).get("properties") or {}
        for field, fschema in props.items():
            lookup = fschema.get("x-lookup")
            must = fschema.get("x-must-resolve", False)
            if lookup and field in raw and raw[field]:
                resolved = None
                if resolver:
                    try:
                        resolved = resolver(lookup, raw[field])
                    except Exception as exc:
                        logger.warning("resolver %s failed: %s", lookup, exc)
                if resolved is not None:
                    raw[field] = resolved
                elif must:
                    raise ValidationError(f"字段 {field} 必须解析为规范值，但无法从资源中匹配")
        try:
            return validate_arguments(raw, tool_spec.input_schema, ctx)
        except SchemaValidationError as exc:
            last_error = str(exc)
            continue
    raise ValidationError("参数编译失败: " + (last_error or "未知错误"))


# ------------------------------------------------------------------
# Reference resolver
# ------------------------------------------------------------------
def reference_resolver(references: Optional[dict] = None):
    """Resolve lookup values from caller supplied, workspace-scoped data.

    The old implementation opened a hard-coded ``demo_finance`` connection
    and used a fuzzy ``LIKE ... LIMIT 1`` query.  Argument compilation is a
    pure transformation step, so it must not choose a value from an unrelated
    database.  Callers may provide a small lookup map/list obtained through
    their already-authorized data-source path; an unknown value remains
    unresolved and is rejected when the schema requires resolution.

    中文补充：入参 ``references`` 形如 ``{"projectId": {...}}``；也接受包在
    ``{"lookups": {...}}`` 里。查找键会兼容 ``xxxId`` 与 ``xxx`` 两种命名。
    候选值支持 dict（label→规范值 或 规范值→label）或 list（元素为 dict 时取
    id/code/value/project_code 作为规范值，name/label/title 作为显示名）。
    返回内部 ``_resolve(lookup, value)`` 函数；未命中返回 None（不抛异常、不查库）。
    """
    supplied = references if isinstance(references, dict) else {}
    if isinstance(supplied.get("lookups"), dict):
        supplied = supplied["lookups"]

    def _resolve(lookup: str, value: str) -> Optional[str]:
        candidates = supplied.get(lookup)
        if candidates is None and lookup.endswith("Id"):
            candidates = supplied.get(lookup[:-2])
        if candidates is None:
            return None
        wanted = str(value).strip()
        if isinstance(candidates, dict):
            if wanted in candidates:
                return candidates[wanted]
            for canonical, label in candidates.items():
                if str(label).strip() == wanted:
                    return canonical
            return None
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict):
                    canonical = (candidate.get("id") or candidate.get("code")
                                 or candidate.get("value") or candidate.get("project_code"))
                    label = (candidate.get("name") or candidate.get("label")
                             or candidate.get("title"))
                    if canonical is not None and (str(canonical).strip() == wanted
                                                   or (label is not None and str(label).strip() == wanted)):
                        return canonical
                elif str(candidate).strip() == wanted:
                    return candidate
        return None

    return _resolve


def project_code_resolver(references: Optional[dict] = None):
    """Backward-compatible name for the caller-supplied reference resolver.

    It intentionally performs no database lookup.  Existing callers can keep
    importing this symbol while migrating to :func:`reference_resolver`.

    中文补充：仅为兼容旧调用点的别名，等价于 ``reference_resolver(references)``；
    新代码应直接使用 reference_resolver，不要在这里加数据库查询逻辑。
    """
    return reference_resolver(references)
