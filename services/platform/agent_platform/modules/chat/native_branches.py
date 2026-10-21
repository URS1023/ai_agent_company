"""原生分支请求的纯计划，不修改消息树、不发HTTP，也不实现模型循环。

依据锁定OpenWebUI0.12.3：Messages.svelte的用户编辑产生同parent的新user；
Chat.svelte的重新生成复用原user并产生新assistant；models/chats.py的insert_chat_turn
在原生事务内保留旧分支并追加节点。本模块只把已授权后台快照装配成该原生合同。
"""

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from typing import Literal

from agent_platform.core.errors import DomainError

BranchKind = Literal['regenerate', 'edit_send']
REPLAY_KEYS = {'id', 'role', 'content', 'output', 'files', 'contextSummary', 'usage', 'model', 'meta'}


def _digest(value):
    try:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise DomainError(422, 'native_chat_branch_content_invalid', '消息内容格式不正确') from None
    if len(data) > 1024 * 1024:
        raise DomainError(422, 'native_chat_context_budget_exceeded', '当前分支内容过多，请选择较短分支')
    return hashlib.sha256(data).hexdigest()


def _file_ids(files):
    if not isinstance(files, list) or len(files) > 10:
        raise DomainError(422, 'native_chat_branch_files_invalid', '该消息附件无法按原引用恢复')
    result = []
    for item in files:
        if (not isinstance(item, dict) or item.get('type') != 'file'
                or not isinstance(item.get('id'), str) or not item['id']):
            raise DomainError(422, 'native_chat_branch_files_invalid', '请先核对该消息的附件映射')
        result.append(item['id'])
    if len(result) != len(set(result)):
        raise DomainError(422, 'native_chat_branch_files_invalid', '该消息包含重复附件引用')
    return tuple(result)


def _message(messages, message_id, role, owner):
    message = messages.get(message_id)
    if (not isinstance(message, dict) or message.get('id') != message_id or message.get('role') != role
            or message.get('user_id', owner) != owner):
        raise DomainError(404, 'native_chat_branch_source_not_found', '原消息不存在或不属于当前对话用户')
    parent = message.get('parentId')
    if parent is not None and (not isinstance(parent, str) or not parent):
        raise DomainError(502, 'native_chat_history_invalid', '原生消息父节点格式不正确')
    return message


def _ancestor_chain(messages, parent_id, owner):
    result, seen, role = [], set(), 'assistant'
    while parent_id is not None:
        if parent_id in seen or len(seen) >= 1000:
            raise DomainError(502, 'native_chat_history_invalid', '原生消息分支存在循环或过长')
        seen.add(parent_id)
        message = _message(messages, parent_id, role, owner)
        if role == 'assistant' and message.get('done') is not True:
            raise DomainError(409, 'native_chat_parent_invalid', '原分支仍有未完成的回答')
        result.append(deepcopy(message))
        parent_id, role = message.get('parentId'), 'user' if role == 'assistant' else 'assistant'
    result.reverse()
    if result and result[0]['role'] != 'user':
        raise DomainError(502, 'native_chat_history_invalid', '原生消息分支缺少起始问题')
    return result


@dataclass(frozen=True)
class BranchSource:
    """后台授权快照；正文只在内存，日志repr不包含消息或附件内容。"""
    intent_kind: BranchKind
    source_message_id: str
    source_user_message_id: str
    parent_message_id: str | None
    snapshot_sha256: str
    native_file_ids: tuple[str, ...]
    known_message_ids: frozenset[str] = field(repr=False)
    user_message: dict = field(repr=False)
    ancestors: tuple[dict, ...] = field(repr=False)


@dataclass(frozen=True)
class BranchPlan:
    """交给现有Turn预约和原生completion的计划，不包含可写的整树。"""
    user_message_id: str
    assistant_message_id: str
    parent_message_id: str | None
    request_metadata: dict
    user_message: dict = field(repr=False)
    messages: list[dict] = field(repr=False)
    files: list[dict] = field(repr=False)


def inspect_branch_source(history, intent_kind: BranchKind, source_message_id, *, native_user_id):
    """仅接read_native_chat得到的原树；调用方先核本人、当前来源及实例，不接客户端history。

    唯一允许非空树中parent为null的分支来自此处验证过的原user；普通发送仍沿原规则。
    快照忽略childrenIds/models等新增兄弟节点可变项，固定实际重放正文、输出和父链。
    """
    if intent_kind not in {'regenerate', 'edit_send'} or not isinstance(native_user_id, str) or not native_user_id:
        raise DomainError(422, 'native_chat_branch_intent_invalid', '请选择明确的消息操作')
    messages = history.get('messages') if isinstance(history, dict) else None
    if not isinstance(messages, dict) or not isinstance(source_message_id, str) or not source_message_id:
        raise DomainError(404, 'native_chat_branch_source_not_found', '原消息不存在')
    source = _message(messages, source_message_id, 'assistant' if intent_kind == 'regenerate' else 'user', native_user_id)
    if intent_kind == 'regenerate' and source.get('done') is not True:
        raise DomainError(409, 'native_chat_regeneration_not_ready', '请先核对原回答的结束状态')
    user_id = source.get('parentId') if intent_kind == 'regenerate' else source_message_id
    user = _message(messages, user_id, 'user', native_user_id)
    if not isinstance(user.get('content'), (str, list)):
        raise DomainError(422, 'native_chat_branch_content_invalid', '原问题内容无法恢复')
    parent = user.get('parentId')
    ancestors = _ancestor_chain(messages, parent, native_user_id)
    chain = [*ancestors, user, *([source] if intent_kind == 'regenerate' else [])]
    for previous, child in zip(chain, chain[1:]):
        children = previous.get('childrenIds')
        if not isinstance(children, list) or child['id'] not in children:
            raise DomainError(502, 'native_chat_history_invalid', '原生消息父子关联不一致')
    files = _file_ids(user.get('files', []))
    replay = [{key: item[key] for key in ('id', 'role', 'parentId', 'content', 'output', 'contextSummary') if key in item}
        | {'file_ids': _file_ids(item.get('files', []))} for item in ancestors]
    snapshot = {'intent_kind': intent_kind, 'source_message_id': source_message_id,
        'user': {'id': user_id, 'parentId': parent, 'content': user['content'], 'file_ids': files},
        'ancestors': replay}
    return BranchSource(intent_kind, source_message_id, user_id, parent, _digest(snapshot), files,
        frozenset(messages), deepcopy(user), tuple(ancestors))


def plan_branch(source: BranchSource, *, assistant_message_id, model_id, timestamp, native_files,
                new_user_message_id=None, content=None, attachment_mode: Literal['inherit', 'replace'] = 'inherit'):
    """映射已验证的分支；native_files必须由平台附件授权解析器产生，不能从前端原样传入。

    重新生成不能更换原题/附件；编辑并发送可继承附件，或显式替换（含[]移除）。
    用户/助手新ID由后台生成，已有父、旧user、旧回答和整树均不在本计划中写入。
    """
    if (not isinstance(assistant_message_id, str) or not assistant_message_id
            or assistant_message_id in source.known_message_ids):
        raise DomainError(409, 'native_chat_branch_id_conflict', '新回答标识与已有消息冲突')
    if (not isinstance(model_id, str) or not model_id or len(model_id) > 200
            or type(timestamp) is not int or timestamp < 0 or attachment_mode not in {'inherit', 'replace'}):
        raise DomainError(422, 'native_chat_branch_intent_invalid', '消息操作参数不正确')
    selected_files = _file_ids(native_files)
    if source.intent_kind == 'regenerate':
        if content is not None or new_user_message_id is not None or attachment_mode != 'inherit':
            raise DomainError(422, 'native_chat_regeneration_changes_user', '重新生成不能修改原问题或附件')
        user_id, text = source.source_user_message_id, deepcopy(source.user_message['content'])
    else:
        if (not isinstance(new_user_message_id, str) or not new_user_message_id
                or new_user_message_id in source.known_message_ids or new_user_message_id == assistant_message_id):
            raise DomainError(409, 'native_chat_branch_id_conflict', '新问题标识与已有消息冲突')
        if not isinstance(content, str) or not 1 <= len(content) <= 40000 or '\x00' in content:
            raise DomainError(422, 'native_chat_branch_content_invalid', '请填写有效的问题内容')
        user_id, text = new_user_message_id, content
    if attachment_mode == 'inherit' and selected_files != source.native_file_ids:
        raise DomainError(403, 'native_chat_branch_files_changed', '原附件引用已变化，请重新核对')
    children = source.user_message.get('childrenIds', []) if source.intent_kind == 'regenerate' else []
    if not isinstance(children, list) or any(not isinstance(item, str) for item in children):
        raise DomainError(502, 'native_chat_history_invalid', '原消息子节点格式不正确')
    user_message = {'id': user_id, 'parentId': source.parent_message_id, 'childrenIds': [*children, assistant_message_id],
        'role': 'user', 'content': text, 'models': [model_id], 'timestamp': timestamp, 'files': deepcopy(native_files)}
    replay = [{key: deepcopy(value) for key, value in item.items() if key in REPLAY_KEYS} for item in source.ancestors]
    replay.append({'role': 'user', 'content': deepcopy(text), 'files': deepcopy(native_files)})
    _digest(replay)
    metadata = {'intent_kind': source.intent_kind, 'source_message_id': source.source_message_id,
        'source_user_message_id': source.source_user_message_id, 'source_snapshot_sha256': source.snapshot_sha256,
        'content_sha256': _digest(text), 'expected_native_file_ids': list(selected_files), 'attachment_mode': attachment_mode}
    return BranchPlan(user_id, assistant_message_id, source.parent_message_id, metadata, user_message, replay, deepcopy(native_files))


def verify_branch_source(history, metadata, *, native_user_id):
    """派发前只核原快照，不把之后变化的原消息当作当时输入；不产生重试或副作用。"""
    if metadata.get('intent_kind') not in {'regenerate', 'edit_send'}:
        return
    current = inspect_branch_source(history, metadata['intent_kind'], metadata.get('source_message_id'), native_user_id=native_user_id)
    if current.snapshot_sha256 != metadata.get('source_snapshot_sha256'):
        raise DomainError(409, 'native_chat_branch_source_changed', '原消息内容或父分支已变化，请核对原操作')


def verify_branch_user(message, metadata):
    """准确助手终态之外再核该次用户正文/附件，避免把被改过的原题当作原预约成功。"""
    if metadata.get('intent_kind') not in {'regenerate', 'edit_send'}:
        return
    if (_digest(message.get('content')) != metadata.get('content_sha256')
            or list(_file_ids(message.get('files', []))) != metadata.get('expected_native_file_ids')):
        raise DomainError(502, 'native_chat_branch_user_changed', '原生问题与本次运行快照不一致')
