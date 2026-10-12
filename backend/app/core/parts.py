# -*- coding: utf-8 -*-
"""Structured Chat message parts (§5.2).

职责：定义助手/用户消息的结构化片段（parts）构造器，统一 `messages.parts_json` 的载荷形状。

系统位置：runtime（agent/service）在流式产出过程中调用这些函数拼装 parts，
chat 模块在读取历史时把 `parts_json` 直接回传给前端；本模块只做纯函数构造，无副作用。

关键约束：
- 每个函数的返回体就是对外契约：`type` 取值与键名被前端渲染器和历史回放（agent.report_history_note
  等）按字符串匹配，新增/改名等于破坏既有会话的渲染，必须同步改前端与回放逻辑。
- parts 以 JSON 落库，值必须是可 JSON 序列化的普通类型；不要把 ORM 对象、datetime 塞进来。
- tool_call 的 id 必须与 tool_result 的 id 一致（配对渲染），由调用方保证。
- `content` 纯文本字段只是前端的降级展示，结构化信息以本模块产物为准。
"""
from __future__ import annotations

from typing import Any, Dict, List


def text_part(text: str) -> dict:
    """文本片段：助手回复正文或用户输入正文（可与其他片段混排）。"""
    return {"type": "text", "text": text}


def status_part(label: str, detail: str = "", state: str = "running") -> dict:
    """过程状态片段（前端进度提示）。

    参数：label 为简短阶段名，detail 为补充说明（可为空），state 为终态标记
    （"running"/"done"/"failed"，前端按此决定加载动画与配色）。
    """
    return {"type": "status", "label": label, "detail": detail, "state": state}


def tool_call_part(tool_call_id: str, name: str, arguments: dict) -> dict:
    """工具调用片段：展示"调用了什么工具、传了什么参数"。

    参数：tool_call_id 必须与同一轮 tool_result_part 的 id 相同，前端据此折叠配对。
    """
    return {"type": "tool_call", "id": tool_call_id, "name": name, "arguments": arguments}


def tool_result_part(tool_call_id: str, tool_name: str, result: dict) -> dict:
    """工具结果片段；result 是已裁剪/脱敏后的对外结构，禁止塞入密钥或原始异常堆栈。"""
    return {"type": "tool_result", "id": tool_call_id, "tool": tool_name, "result": result}


def citation_part(citation: dict) -> dict:
    """引用片段：知识库检索命中的出处，citation 形状与 citations 表/引用接口保持一致。"""
    return {"type": "citation", "citation": citation}


def artifact_part(artifact: dict) -> dict:
    """产物片段：本轮运行生成的文档/图表等，artifact 需含前端下载所需的 id、文件名等字段。"""
    return {"type": "artifact", "artifact": artifact}


def error_part(message: str, code: str = "error") -> dict:
    """错误片段：把失败原因展示在消息流里（不中断整条会话历史的可读性）。

    code 与 app.errors 的错误码体系保持一致，前端按 code 分支提示。
    """
    return {"type": "error", "message": message, "code": code}


def file_part(file_info: dict) -> dict:
    """附件片段：用户随消息上传的文件元信息（含 file_id，供后续工具读取）。"""
    return {"type": "file", "file": file_info}
