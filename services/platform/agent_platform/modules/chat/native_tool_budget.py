"""对准确原生写请求签发独立预算头；JWT只在后台HTTP内存中存在。"""

from datetime import timezone
import hashlib
import json
import time

import jwt

from agent_platform.core.errors import DomainError
from .native_tool_policy import frozen_tool_policy
from .native_models import NativeTurnAction

HEADER = 'X-AIC-Native-Turn-Budget'
TOKEN_TYPE = 'aic-native-turn-budget-v1'
ISSUER = 'ai-agent-company'
AUDIENCE = 'openwebui-native-turn-budget-v1'
PATCH_ID = 'openwebui-0.12.3-platform-turn-budget-v1'
CAPABILITY_PATH = '/api/aic-native/budget'


def canonical_body(value):
    """与锁定原生补丁共用的有限JSON编码，包含最终注入的Socket session_id。"""
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError):
        raise DomainError(422, 'native_chat_budget_body_invalid', '本次对话请求内容不正确') from None
    if len(encoded) > 2 * 1024 * 1024:
        raise DomainError(422, 'native_chat_context_budget_exceeded', '本次对话内容过多')
    return encoded


def verify_capability(value, policy):
    """旧原生忽略未知请求头，所以受管别名必须先核真实预算实现已经就绪。"""
    expected = {'version', 'patch_id', 'alias_model_id', 'server_id', 'ledger_ready'}
    if (not isinstance(value, dict) or set(value) != expected or type(value['version']) is not int or value['version'] != 1
            or value['patch_id'] != PATCH_ID or value['alias_model_id'] != policy['alias_model_id']
            or value['server_id'] != policy['server_id'] or value['ledger_ready'] is not True):
        raise DomainError(503, 'native_chat_budget_not_ready', '业务工具模型的调用预算尚未就绪')


def budget_header(request, session, actor, turn, identity, path, payload, *, action_id=None, now=None):
    """签名首次completion或准确resolution，恢复仍绑定原截止和原模型预算。

    action_id必须来自已持久化NativeTurnAction；调用者不能用前端nonce或请求中的预算。
    原生端须在批准副作用前验证resolution，重建payload后复用同一已验声明。
    """
    policy = frozen_tool_policy(turn)
    if policy is None:
        return None
    actor.require('m02.write')
    if (turn.user_id != actor.user_id or turn.workspace_id != actor.workspace_id or turn.identity_id != identity.id
            or identity.user_id != actor.user_id or identity.workspace_id != actor.workspace_id
            or turn.integration_id != identity.integration_id or not identity.native_user_id):
        raise DomainError(403, 'native_chat_budget_binding_invalid', '对话预算归属不一致')
    expected_path = '/api/chat/completions' if action_id is None else (
        '/api/v1/chats/' + turn.native_chat_id + '/messages/' + turn.assistant_message_id + '/resolve')
    if path != expected_path:
        raise DomainError(403, 'native_chat_budget_binding_invalid', '对话预算不适用于此操作')
    if not isinstance(payload, dict):
        raise DomainError(403, 'native_chat_budget_binding_invalid', '对话预算不适用于此请求内容')
    if action_id is None:
        user_message = payload.get('user_message')
        params = payload.get('params')
        if (payload.get('model') != turn.model_id or payload.get('chat_id') != turn.native_chat_id
                or payload.get('id') != turn.assistant_message_id or not isinstance(user_message, dict)
                or user_message.get('id') != turn.user_message_id or user_message.get('parentId') != turn.parent_message_id
                or 'tools' in payload or payload.get('tool_ids') != ['server:' + policy['server_id']]
                or not isinstance(params, dict) or params.get('function_calling') != 'native'):
            raise DomainError(403, 'native_chat_budget_binding_invalid', '对话预算与预约消息或工具不一致')
    if action_id is not None:
        action = session.get(NativeTurnAction, action_id)
        if (action is None or action.turn_id != turn.id or action.workspace_id != actor.workspace_id
                or action.user_id != actor.user_id or action.kind != 'resolve' or action.status != 'dispatching'):
            raise DomainError(403, 'native_chat_budget_binding_invalid', '对话恢复动作尚未预约或不属于当前运行')
        metadata = action.request_metadata or {}
        if (set(payload) - {'call_id', 'action', 'answers'} or payload.get('call_id') != metadata.get('call_id')
                or payload.get('action') != metadata.get('action')
                or hashlib.sha256(canonical_body(payload.get('answers'))).hexdigest() != metadata.get('answers_sha256')):
            raise DomainError(403, 'native_chat_budget_binding_invalid', '对话恢复内容与原预约不一致')
    deadline = turn.deadline_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    issued, expires = int(time.time() if now is None else now), int(deadline.timestamp())
    if not 0 < expires - issued <= policy['max_seconds']:
        raise DomainError(504, 'native_chat_deadline_exceeded', '本次对话已达到原运行时限')
    max_tokens = (turn.request_metadata or {}).get('max_tokens')
    if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
        raise DomainError(503, 'native_chat_tool_policy_invalid', '本次运行的输出预算无法核对')
    vault = getattr(request.app.state, 'secret_vault', None)
    if vault is None:
        raise DomainError(503, 'secret_store_unavailable', '凭据存储尚未就绪')
    secret = vault.get(policy['budget_secret_ref'], actor.workspace_id, session=session)
    if not isinstance(secret, str) or not 32 <= len(secret) <= 4096 or '\x00' in secret:
        raise DomainError(503, 'native_chat_budget_secret_invalid', '预算凭据已不可用')
    claims = {'iss': ISSUER, 'aud': AUDIENCE, 'v': 1, 'jti': turn.id, 'sub': identity.native_user_id,
        'workspace_id': turn.workspace_id, 'integration_id': turn.integration_id, 'chat_id': turn.native_chat_id,
        'user_message_id': turn.user_message_id, 'message_id': turn.assistant_message_id, 'model_id': turn.model_id,
        'server_id': policy['server_id'], 'policy_revision': policy['integration_revision'],
        'max_tool_calls': policy['max_tool_calls'], 'max_tool_iterations': policy['max_tool_iterations'],
        'max_model_requests': policy['max_model_requests'], 'max_tokens': max_tokens, 'max_seconds': policy['max_seconds'],
        'deadline': expires, 'iat': issued, 'exp': expires, 'dispatch_kind': 'resolution' if action_id else 'completion',
        'dispatch_nonce': action_id or turn.id, 'request_path': path,
        'body_sha256': hashlib.sha256(canonical_body(payload)).hexdigest()}
    return jwt.encode(claims, secret, algorithm='HS256', headers={'typ': TOKEN_TYPE})
