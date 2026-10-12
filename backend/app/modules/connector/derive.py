# -*- coding: utf-8 -*-
"""将接口说明推导为连接器草稿，并检查模型输出的基本结构。

- 输入：用户粘贴的接口说明文本；输出：一份**未受信任**的 Connector 草稿定义
  （含 tools 与 input_schema）。属于"辅助填写"能力，供管理台预填表单。
- 安全约束：模型产出必须由调用方走 capabilities.service.normalize_configuration /
  _schema_contract 校验后才能落库；本模块只做解析与类型校验，不做地址白名单、
  不检查地址与凭据（因此绝不能被直接用于创建已发布连接器）。
- 模型格式错误返回 ValidationError；权限、取消和模型调用异常保留原有语义。
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError as ModelValidationError

from ...errors import ValidationError
from ...providers.model import chat_for_run

_DERIVE_SYSTEM = """你是一个企业系统集成助手。用户会粘贴一个现有程序的 API 描述（包括用途、请求、返回）。
请分析并生成统一的结构化 Connector 定义，只输出 JSON，不要多余文字。

要求的 JSON 结构：
{
  "name": "连接器名称（中文，简短）",
  "base_url": "基础URL，无法推断时填空字符串",
  "path": "接口路径，例如 /api/render-ppt，无法推断时填空",
  "method": "GET|POST|PUT|PATCH|DELETE",
  "body_mapping": "json|form|query",
  "auth": {"type": "none|bearer|basic"},
  "timeout_seconds": 120,
  "tools": [
    {
      "name": "工具名（英文小写下划线，如 generate_ppt）",
      "description": "工具用途的详细自然语言说明（尽量包含参数语义/示例/约束，供模型理解）",
      "input_schema": {"type": "object", "properties": {每个参数: {"type": "...", "description": "语义说明"}}, "required": [...]}
    }
  ]
}

约束：
- input_schema 中的每个参数必须有充分的中文语义描述、类型、示例；枚举要列 enum；日期给 format=date，并在 description/examples 中说明 YYYY-MM-DD 的输入形态。
- tools 至少一个；名称简单直接。
- 忠实保留原接口的 HTTP 方法与请求体映射；不支持的方式由系统明确提示，禁止改写接口语义。
"""


class _DerivedTool(BaseModel):
    """先限制模型工具字段的类型，详细 JSON Schema 仍交给能力中心校验。"""

    model_config = ConfigDict(strict=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)


class _DerivedDefinition(BaseModel):
    """模型必须给出对象、合法类型和有限工具集合，不将字符串或布尔量强转为数值。"""

    model_config = ConfigDict(strict=True)
    name: str = Field(default="新连接器", min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    base_url: str = ""
    path: str = "/"
    method: str = Field(default="POST", min_length=1)
    body_mapping: str = Field(default="json", min_length=1)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    tools: list[_DerivedTool] = Field(min_length=1, max_length=50)


def _strip_fences(text: str) -> str:
    """去掉模型回复首尾的 ``` 代码围栏（只处理整行围栏，避免误删正文内容）。"""
    fence = "`" * 3
    lines = text.split("\n")
    if lines and lines[0].strip().startswith(fence):
        lines = lines[1:]
    if lines and lines[-1].strip() == fence:
        lines = lines[:-1]
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """用标准 JSON 解码器读取对象，正确处理字符串中的括号、转义与说明文字。"""
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("AI 未返回有效的连接器 JSON 文本")
    text = _strip_fences(text.strip())

    def reject_constant(_value):
        raise ValidationError("AI 返回的 JSON 含非有限数值")

    decoder = json.JSONDecoder(parse_constant=reject_constant)
    try:
        plan = decoder.decode(text)
    except json.JSONDecodeError:
        starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not starts:
            raise ValidationError("无法从 AI 结果中解析 JSON 对象") from None
        try:
            # 只解析第一个容器，不能把数组或破损外层中的内层对象当成完整定义。
            plan, _end = decoder.raw_decode(text, min(starts))
        except (ValueError, RecursionError):
            raise ValidationError("AI 返回的 JSON 不完整或嵌套过深，请补充清晰的接口说明") from None
    except (ValueError, RecursionError):
        raise ValidationError("AI 返回的 JSON 不完整或嵌套过深，请补充清晰的接口说明") from None
    if not isinstance(plan, dict):
        raise ValidationError("AI 返回的连接器定义必须是 JSON 对象")
    return plan


def derive_definition(workspace_id: str, description: str) -> dict:
    """按工作区调用模型并检查草稿类型；地址、权限和工具契约仍须在保存前校验。"""
    resp = chat_for_run("reasoning", [
        {"role": "system", "content": _DERIVE_SYSTEM},
        {"role": "user", "content": "请分析以下程序接口并生成 Connector 定义：\n" + description},
    ], temperature=0.1, workspace_id=workspace_id)
    if not isinstance(resp, dict):
        raise ValidationError("AI 未返回有效的连接器定义响应")
    plan = _extract_json(resp.get("content"))
    try:
        _DerivedDefinition.model_validate(plan)
    except ModelValidationError as exc:
        fields = [".".join(map(str, item["loc"])) for item in exc.errors(include_input=False)][:10]
        raise ValidationError("AI 返回的连接器字段类型或范围不正确，请核对：" + "、".join(fields)) from None
    return plan
