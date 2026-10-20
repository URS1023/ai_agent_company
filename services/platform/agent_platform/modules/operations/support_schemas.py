"""系统辅助工具入参：只保存声明式配置，不接收代码执行或数据库连接。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class SupportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=160)
    scope: Literal["workspace", "personal"] = "workspace"
    enabled: bool = True
    definition: dict = Field(default_factory=dict)


class SupportPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    scope: Literal["workspace", "personal"] | None = None
    enabled: bool | None = None
    definition: dict | None = None


class SupportRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    target_revision: int = Field(ge=1)


class FormValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    values: dict = Field(default_factory=dict)
