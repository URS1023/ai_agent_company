"""身份配置使用显式字段，拒绝误传超级管理员标记和其他控制字段。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IdentityInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class RevisionInput(IdentityInput):
    revision: int = Field(ge=1)

    @model_validator(mode='after')
    def required_changes_not_null(self):
        for name in self.model_fields_set:
            if name not in {'department_id', 'password'} and getattr(self, name) is None:
                raise ValueError('必填字段不能置空')
        return self


class WorkspaceCreate(IdentityInput):
    name: str = Field(min_length=1, max_length=160)
    factory_id: str = Field(default='', max_length=80)
    industry: str = Field(default='制造业', max_length=80)


class WorkspacePatch(RevisionInput):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    factory_id: str | None = Field(default=None, max_length=80)
    industry: str | None = Field(default=None, max_length=80)
    archived: bool | None = None


class RoleCreate(IdentityInput):
    name: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(default_factory=list, max_length=100)


class RolePatch(RevisionInput):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    permissions: list[str] | None = Field(default=None, max_length=100)


class MemberCreate(IdentityInput):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    username: str = Field(min_length=3, max_length=100, pattern=r'^[\w.@-]+$')
    password: str | None = Field(default=None, min_length=12, max_length=256)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role_ids: list[str] = Field(default_factory=list, max_length=100)
    department_id: str | None = None


class MemberPatch(RevisionInput):
    role_ids: list[str] | None = Field(default=None, max_length=100)
    department_id: str | None = None
    disabled: bool | None = None


class WorkspaceSelection(IdentityInput):
    workspace_id: str = Field(min_length=1, max_length=36)


class GrantCreate(IdentityInput):
    resource_type: str = Field(min_length=1, max_length=100)
    resource_id: str = Field(min_length=1, max_length=80)
    user_id: str = Field(min_length=1, max_length=36)
    actions: list[Literal['read', 'write', 'delete']] = Field(default_factory=lambda: ['read'], min_length=1, max_length=3)
