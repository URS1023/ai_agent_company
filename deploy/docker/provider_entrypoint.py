"""在容器内解密本轮配置，随后执行原版启动脚本；密钥不进入OCI配置或命令行。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from cryptography.fernet import Fernet

RUNTIME = Path('/run/agent-runtime')
CHAT_URL = 'https://opencode.ai/inference/go/openai/v1'
CHAT_MODEL = 'deepseek-v4.1-flash'
EMBEDDING_URL = 'https://api.siliconflow.cn/v1'
EMBEDDING_MODEL = 'BAAI/bge-m3'

# 不继承镜像中可能存在的旧数据库、代理、托管空间自动注册或其他模型提供方配置。
BLOCKED_PREFIXES = ('DATABASE_', 'REDIS_', 'VALKEY_', 'WEBSOCKET_REDIS_', 'CHROMA_',
    'QDRANT_', 'PGVECTOR_', 'MILVUS_', 'PINECONE_', 'OPENSEARCH_', 'ELASTICSEARCH_',
    'WEAVIATE_', 'ORACLE_', 'MARIADB_', 'MYSQL_', 'PGVECTO_RS_', 'S3_', 'GCS_', 'AZURE_',
    'OPENAI_', 'RAG_OPENAI_', 'OLLAMA_', 'ANTHROPIC_', 'GEMINI_', 'ADMIN_USER_', 'SPACE_')
BLOCKED_NAMES = {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'VECTOR_DB',
    'WEBUI_JWT_SECRET_KEY', 'WEBUI_SECRET_KEY', 'WEBSOCKET_MANAGER', 'UVICORN_RELOAD',
    'DATABASE_URL', 'FRONTEND_BUILD_DIR', 'ENABLE_PERSISTENT_CONFIG', 'PYTHONPATH'}


def validate_credentials(value: dict) -> dict:
    """仅接受本轮已批准的两家提供方，不接受任意URL或客户端注入的存储参数。"""
    if not isinstance(value, dict) or not isinstance(value.get('webui_secret'), str) or len(value['webui_secret']) < 32:
        raise ValueError('invalid_runtime_secret')
    providers = value.get('provider_settings')
    if not isinstance(providers, dict):
        raise ValueError('missing_provider_settings')
    for role, url, model in [('chat', CHAT_URL, CHAT_MODEL), ('embedding', EMBEDDING_URL, EMBEDDING_MODEL)]:
        data = providers.get(role)
        if not isinstance(data, dict) or data.get('base_url') != url or data.get('model') != model:
            raise ValueError('provider_scope_mismatch')
        key = data.get('api_key')
        if not isinstance(key, str) or not key.strip() or any(c in key for c in '\r\n\x00'):
            raise ValueError('invalid_provider_key')
    if providers['chat'].get('provider') != 'opencode-go':
        raise ValueError('provider_scope_mismatch')
    return value


def build_environment(value: dict, inherited: dict[str, str]) -> dict[str, str]:
    """保留原生UI和外部API功能，强制本次SQLite/嵌入式Chroma与离线权重策略。"""
    value = validate_credentials(value)
    env = {key: val for key, val in inherited.items()
        if key.upper() not in BLOCKED_NAMES and not key.upper().startswith(BLOCKED_PREFIXES)}
    chat, embedding = value['provider_settings']['chat'], value['provider_settings']['embedding']
    env.update({
        'WEBUI_SECRET_KEY': value['webui_secret'], 'WEBUI_AUTH': 'True', 'ENABLE_SIGNUP': 'True',
        'ENABLE_PERSISTENT_CONFIG': 'False', 'ENABLE_OPENAI_API': 'True', 'ENABLE_OLLAMA_API': 'False',
        'OPENAI_API_BASE_URL': chat['base_url'], 'OPENAI_API_BASE_URLS': chat['base_url'],
        'OPENAI_API_KEY': chat['api_key'], 'OPENAI_API_KEYS': chat['api_key'],
        'OPENAI_API_CONFIGS': json.dumps({'0': {'enable': True, 'model_ids': [chat['model']],
            'headers': {'User-Agent': 'manufacturing-agent-platform/0.1 OpenWebUI',
                'x-opencode-session': 'owui-{{USER_ID}}-{{CHAT_ID}}'}}}),
        'DEFAULT_MODELS': chat['model'], 'RAG_EMBEDDING_ENGINE': 'openai',
        'RAG_OPENAI_API_BASE_URL': embedding['base_url'], 'RAG_OPENAI_API_KEY': embedding['api_key'],
        'RAG_EMBEDDING_MODEL': embedding['model'], 'DATA_DIR': '/app/backend/data',
        'DATABASE_URL': 'sqlite:////app/backend/data/webui.db', 'VECTOR_DB': 'chroma',
        'CHROMA_HTTP_HOST': '', 'CHROMA_HTTP_PORT': '8000', 'REDIS_URL': '', 'REDIS_SENTINEL_HOSTS': '',
        'WEBSOCKET_REDIS_URL': '', 'WEBSOCKET_MANAGER': '', 'ENABLE_WEBSOCKET_SUPPORT': 'True',
        'PYTHON_DOTENV_DISABLED': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'OFFLINE_MODE': 'True', 'HF_HOME': '/app/backend/data/model-cache',
        'HUGGINGFACE_HUB_CACHE': '/app/backend/data/model-cache/hub',
        'RAG_EMBEDDING_MODEL_AUTO_UPDATE': 'False', 'RAG_RERANKING_MODEL_AUTO_UPDATE': 'False',
        'WHISPER_MODEL_AUTO_UPDATE': 'False', 'USE_OLLAMA_DOCKER': 'False', 'USE_CUDA_DOCKER': 'False',
        'WEB_LOADER_ENGINE': 'requests', 'ENABLE_VERSION_UPDATE_CHECK': 'False',
        'ENABLE_BASE_MODELS_CACHE': 'False', 'ENABLE_AUTOMATIONS': 'False', 'ENABLE_CALENDAR': 'False',
        'TIMER_POLL_INTERVAL': '60', 'SCHEDULER_POLL_INTERVAL': '60',
        'DO_NOT_TRACK': 'True', 'ANONYMIZED_TELEMETRY': 'False',
        'THREAD_POOL_SIZE': '2', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
        'OPENBLAS_NUM_THREADS': '1', 'TOKENIZERS_PARALLELISM': 'false',
        'HOST': '0.0.0.0', 'PORT': '8080', 'UVICORN_WORKERS': '1',
    })
    return env


def read_credentials(directory: Path = RUNTIME) -> dict:
    """只读固定挂载文件；控制器负责验证其只读绑定、来源和摘要。"""
    paths = [directory / 'runtime.key', directory / 'credentials.enc']
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError('invalid_credential_mount')
    key, ciphertext = (path.read_bytes() for path in paths)
    return validate_credentials(json.loads(Fernet(key.strip()).decrypt(ciphertext)))


def main() -> int:
    """不导入应用、不打印环境；解密失败只输出固定错误码后退出。"""
    try:
        env = build_environment(read_credentials(), dict(os.environ))
        os.execve('/bin/bash', ['/bin/bash', '/app/backend/start.sh'], env)
    except Exception:
        print('agent_runtime_configuration_failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
