"""验证本机模型配置不落明文，并使用真实会话占位符构造原生配置。"""
import json

from agent_platform.core.provider_settings import LocalProviderStore


def test_provider_keys_are_encrypted_and_only_child_environment_contains_them(tmp_path):
    store = LocalProviderStore(tmp_path / 'providers')
    metadata = store.write('isolated-chat-key', 'isolated-embedding-key')
    assert b'isolated-chat-key' not in store.path.read_bytes()
    assert b'isolated-embedding-key' not in store.path.read_bytes()
    assert 'api_key' not in json.dumps(metadata)
    assert metadata['chat']['model'] == 'deepseek-v4.1-flash'
    env = store.openwebui_environment()
    assert env['OPENAI_API_KEY'] == 'isolated-chat-key'
    assert env['RAG_OPENAI_API_KEY'] == 'isolated-embedding-key'
    assert '/go/' in env['OPENAI_API_BASE_URL']
    config = json.loads(env['OPENAI_API_CONFIGS'])['0']
    assert config['headers']['x-opencode-session'] == 'owui-{{USER_ID}}-{{CHAT_ID}}'
    assert config['model_ids'] == ['deepseek-v4.1-flash']
