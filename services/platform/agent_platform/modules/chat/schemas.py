"""对话请求结构：消息与预算在入口处校验，业务层只处理合法请求。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    role: Literal['system', 'user', 'assistant']
    content: str = Field(max_length=40000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    messages: list[ChatModel] = Field(min_length=1, max_length=80)
    max_tokens: int = Field(default=1600, ge=64, le=4096)
    temperature: float = Field(default=0.3, ge=0, le=2)
    # 前端传入稳定会话标识，上游据此复用路由；缺省时由后端生成。
    session: str | None = Field(default=None, max_length=80)
