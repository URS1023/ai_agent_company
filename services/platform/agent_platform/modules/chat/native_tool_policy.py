"""后台工具别名策略及不可变Turn预算；不读取原生数据库、不执行工具。"""

import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from typing import Literal

from agent_platform.core.errors import DomainError

CONFIG_FIELDS = {'chat_tool_policy', 'chat_budget_secret_ref'}
PIN = re.compile(r'[A-Za-z0-9_-]{1,80}')
MODEL = re.compile(r'[^\s\x00-\x20]{1,300}')


class ToolPolicy(BaseModel):
    """管理员边界；普通浏览器运行DTO不包含这些字段。"""
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal[1] = 1
    alias_model_id: str = Field(min_length=1, max_length=300)
    server_id: str = Field(min_length=1, max_length=80)
    max_tool_calls: int = Field(default=4, ge=1, le=32)
    max_tool_iterations: int = Field(default=2, ge=1, le=16)
    max_model_requests: int = Field(default=3, ge=1, le=32)
    max_seconds: int = Field(default=90, ge=1, le=90)

    @field_validator('version', mode='before')
    @classmethod
    def exact_version(cls, value):
        """Literal的相等比较会接纳True，协议版本必须是实际整数。"""
        if type(value) is not int or value != 1:
            raise ValueError('工具预算版本必须为整数1')
        return value


def _policy(value):
    try:
        policy = ToolPolicy.model_validate(value).model_dump()
        if (type(value.get('version', 1)) is not int or policy['alias_model_id'] == '*' or not MODEL.fullmatch(policy['alias_model_id'])
                or not PIN.fullmatch(policy['server_id'])):
            raise ValueError()
        return policy
    except (ValidationError, AttributeError, ValueError, TypeError):
        raise DomainError(422, 'native_chat_tool_policy_invalid', '业务工具模型策略不正确',
            {'chat_tool_policy': '请选择具体模型、工具连接及有效调用上限'}) from None


def configured_tool_policy(data):
    """读取集成策略；只有同时清空策略与密钥引用才表示关闭。"""
    value, ref = data.get('chat_tool_policy'), data.get('chat_budget_secret_ref')
    if value is None and ref is None:
        return None
    if value is None or not isinstance(ref, str) or not PIN.fullmatch(ref):
        raise DomainError(422, 'native_chat_tool_policy_invalid', '请同时配置业务工具策略和独立预算凭据')
    policy = _policy(value)
    if (data.get('engine') != 'openwebui' or policy['alias_model_id'] not in (data.get('chat_model_ids') or [])
            or not data.get('chat_model_group_id') or not data.get('delegation_service_identity_id')
            or not data.get('delegation_jwt_secret_ref') or ref == data['delegation_jwt_secret_ref']):
        raise DomainError(422, 'native_chat_tool_policy_invalid', '请先配置专属模型授权与可信工具身份')
    return policy


def validate_tool_policy_config(actor, data, *, existing=None, vault=None, session=None):
    """仅后台管理配置；预算密钥与工具用户签名密钥必须是独立引用、独立值。"""
    changed = any(data.get(key) != (existing or {}).get(key) for key in CONFIG_FIELDS)
    if changed:
        actor.require('m15.admin')
    policy = configured_tool_policy(data)
    if policy is None:
        return
    if vault is None:
        raise DomainError(503, 'secret_store_unavailable', '凭据存储尚未就绪')
    try:
        secret = vault.get(data['chat_budget_secret_ref'], actor.workspace_id, session=session)
        forwarding = vault.get(data['delegation_jwt_secret_ref'], actor.workspace_id, session=session)
    except DomainError:
        raise DomainError(422, 'native_chat_budget_secret_invalid', '请选择当前工作区的独立预算凭据') from None
    if (not isinstance(secret, str) or not 32 <= len(secret) <= 4096 or '\x00' in secret or secret == forwarding):
        raise DomainError(422, 'native_chat_budget_secret_invalid', '预算凭据必须独立且至少32个字符')


def freeze_tool_policy(integration, model_id):
    """在预约事务固定选中别名的上限；普通模型不附加工具，也不继承现在的工具策略。"""
    policy = configured_tool_policy(integration)
    if policy is None or model_id != policy['alias_model_id']:
        return None
    return {**policy, 'integration_revision': integration['revision'],
        'budget_secret_ref': integration['chat_budget_secret_ref']}


def validate_frozen_policy(value, *, model_id):
    """只校历史JSON，不查当前集成。缺失表示旧Turn，畸形策略须拒绝而非降级。"""
    if value is None:
        return None
    expected = set(ToolPolicy.model_fields) | {'integration_revision', 'budget_secret_ref'}
    try:
        if (not isinstance(value, dict) or set(value) != expected
                or type(value['integration_revision']) is not int or value['integration_revision'] < 1
                or not isinstance(value['budget_secret_ref'], str) or not PIN.fullmatch(value['budget_secret_ref'])):
            raise ValueError()
        policy = _policy({key: value[key] for key in ToolPolicy.model_fields})
        if policy['alias_model_id'] != model_id:
            raise ValueError()
        return {**policy, 'integration_revision': value['integration_revision'], 'budget_secret_ref': value['budget_secret_ref']}
    except (DomainError, KeyError, ValueError, TypeError):
        raise DomainError(503, 'native_chat_tool_policy_invalid', '本次运行的工具预算无法核对') from None


def frozen_tool_policy(turn):
    """给工具派发/计数共用的只读入口，绝不以当前绑定覆盖历史上限。"""
    metadata = turn.request_metadata
    if not isinstance(metadata, dict):
        raise DomainError(503, 'native_chat_tool_policy_invalid', '本次运行的工具预算无法核对')
    return validate_frozen_policy(metadata.get('tool_policy'), model_id=turn.model_id)


def verify_alias_model(item, policy):
    """原生目录必须返回明确OpenAI别名并关闭内建工具，避免暗中注入shell或子Agent。"""
    info = item.get('info') if isinstance(item, dict) else None
    meta = info.get('meta') if isinstance(info, dict) else None
    capabilities = meta.get('capabilities') if isinstance(meta, dict) else None
    if (not isinstance(item, dict) or item.get('id') != policy['alias_model_id'] or item.get('owned_by') != 'openai' or item.get('pipe')
            or not isinstance(info, dict) or not isinstance(info.get('base_model_id'), str) or not info['base_model_id']
            or info['base_model_id'] == item['id'] or not isinstance(capabilities, dict)
            or capabilities.get('builtin_tools') is not False):
        raise DomainError(503, 'native_chat_tool_alias_invalid', '业务工具模型尚未完成受控配置')
