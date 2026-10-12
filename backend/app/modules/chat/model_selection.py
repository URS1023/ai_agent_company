# 对话可用模型的检索与校验（模块 docstring 见下）：只返回当前工作区"可用且已配置密钥"
# 的对话模型，绝不返回 endpoint、api_key 或管理端字段，避免对话侧接口泄漏模型配置。
#
# 位置：被 `chat/router.py` 的 `GET /api/chat/models`（前端右侧模型选择器）与
# `POST /conversations/{id}/send`（发送前解析 model_profile）调用；
# 数据源是 `modules/model/service.list_effective_profiles`（含工作区级覆盖与环境默认）。
#
# 约束：优先使用启用的 `reasoning`，否则使用候选列表首项；发送时的 model_profile 必须来自本模块的候选集合，
# 否则抛 ValidationError——不允许客户端提交任意 profile 名绕过可用性校验。
"""Workspace-scoped chat choices without administration secrets."""
from app.errors import ValidationError
from app.modules.model.service import list_effective_profiles


def chat_models(db, workspace_id: str) -> list[dict]:
    """列出可用于对话的工作区模型档位。

    过滤条件：能力包含 chat 或 reasoning、已启用、已配置 api_key、且 endpoint 与 model 均非空
    （缺任一都发不出请求，提前过滤可避免把不可用档位暴露给用户）。
    返回：[{profile_name, model, label, default}]，只含展示所需字段。
    """
    items = [{'profile_name': item['profile_name'], 'model': item['model'],
              'label': item['model'], 'default': item['profile_name'] == 'reasoning'}
             for item in list_effective_profiles(db, workspace_id)
             if {'chat', 'reasoning'} & set(item.get('capabilities') or [])
             and item['enabled'] and item.get('api_key_configured')
             and item.get('model') and item.get('endpoint')]
    # reasoning 只是历史默认名；如果被停用，首个已配置档位仍应能接住新会话。
    if items and not any(item['default'] for item in items):
        items[0]['default'] = True
    return items


def selected_model(db, workspace_id: str, profile_name: str | None) -> str:
    """把请求里的模型档位解析成实际使用的档位名。

    `profile_name` 为 None 表示前端未指定，选择工作区标记的默认档位或首个可用档位；
    指定了但不在当前可用集合中则抛 ValidationError，提示用户刷新模型列表。
    只读，无副作用。
    """
    choices = chat_models(db, workspace_id)
    if profile_name is None:
        if not choices:
            raise ValidationError('当前工作区没有可用的对话模型，请先配置模型密钥和接口')
        return next(item['profile_name'] for item in choices if item['default'])
    if profile_name not in {item['profile_name'] for item in choices}:
        raise ValidationError('所选对话模型不可用，请刷新模型列表后重新选择')
    return profile_name
