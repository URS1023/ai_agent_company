"""管理员配置原生聊天的专属组与模型范围；配置验证不发原生HTTP或模型请求。"""

import re

from agent_platform.core.errors import DomainError

FIELDS = ('chat_model_group_id', 'chat_model_ids')


def chat_model_policy(data):
    """返回规范化策略；两个空字段表示未配置或显式关闭，不隐式开放所有模型。"""
    group, models = data.get(FIELDS[0]), data.get(FIELDS[1])
    if group is None and models in (None, []):
        return None
    if (not isinstance(group, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', group)
            or not isinstance(models, list) or not 1 <= len(models) <= 32
            or any(not isinstance(value, str) or not 1 <= len(value) <= 300
                or value == '*' or any(char.isspace() or ord(char) < 33 for char in value) for value in models)
            or len(set(models)) != len(models)):
        raise DomainError(422, 'chat_model_policy_invalid', '请同时选择专属聊天组和有效模型',
            {FIELDS[0]: '请选择专属聊天组', FIELDS[1]: '请选择不重复的具体模型'})
    if data.get('engine') != 'openwebui':
        raise DomainError(422, 'chat_model_policy_engine_invalid', '该模型策略仅适用于Open WebUI')
    return {'group_id': group, 'model_ids': list(models)}


def validate_chat_model_config(actor, data, *, existing=None):
    """只有新增、变更或清除策略需要管理权；普通配置编辑不会重授模型权限。"""
    before = tuple((existing or {}).get(key) for key in FIELDS)
    after = tuple(data.get(key) for key in FIELDS)
    if before != after and not (existing is None and after in ((None, None), (None, []))):
        actor.require('m15.admin')
    return chat_model_policy(data)
