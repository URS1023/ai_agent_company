"""本机原生服务的加密模型配置；与测试数据分离，子进程启动时才解密注入。"""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from cryptography.fernet import Fernet

from .settings import PROJECT_ROOT

DEFAULT_DIRECTORY = PROJECT_ROOT / '.local' / 'providers'
CHAT_MODEL = 'deepseek-v4.1-flash'
EMBEDDING_MODEL = 'BAAI/bge-m3'
CHAT_BASE_URL = 'https://opencode.ai/inference/go/openai/v1'
EMBEDDING_BASE_URL = 'https://api.siliconflow.cn/v1'


class LocalProviderStore:
    """保存初装模型配置；密钥文件和密文都只位于已忽略的本机目录。"""
    def __init__(self, directory: Path = DEFAULT_DIRECTORY):
        self.directory = Path(directory).resolve()
        self.path = self.directory / 'providers.enc'
        self.key_path = self.directory / 'master.key'

    def _cipher(self, *, create=False):
        configured = os.environ.get('AGENT_PROVIDER_MASTER_KEY')
        if configured:
            return Fernet(configured.encode())
        if not self.key_path.exists():
            if not create:
                raise RuntimeError('本机模型配置尚未初始化')
            self.directory.mkdir(parents=True, exist_ok=True)
            try:
                with self.key_path.open('xb') as handle:
                    handle.write(Fernet.generate_key())
                self.key_path.chmod(0o600)
            except FileExistsError:
                pass
        return Fernet(self.key_path.read_bytes().strip())

    def write(self, chat_key: str, embedding_key: str) -> dict:
        if not chat_key.strip() or not embedding_key.strip():
            raise ValueError('请填写两个服务的密钥')
        payload = {'chat': {'provider': 'opencode-go', 'base_url': CHAT_BASE_URL, 'model': CHAT_MODEL, 'api_key': chat_key.strip()},
                   'embedding': {'base_url': EMBEDDING_BASE_URL, 'model': EMBEDDING_MODEL, 'api_key': embedding_key.strip()}}
        cipher = self._cipher(create=True)
        # 先写临时文件后替换，失败时保留原配置，不产生半个JSON或明文文件。
        with NamedTemporaryFile(dir=self.directory, delete=False) as handle:
            temp = Path(handle.name)
            handle.write(cipher.encrypt(json.dumps(payload).encode()))
        try:
            temp.chmod(0o600)
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)
        return self.metadata()

    def read(self) -> dict:
        return json.loads(self._cipher().decrypt(self.path.read_bytes()))

    def metadata(self) -> dict:
        value = self.read()
        return {role: {'base_url': data['base_url'], 'model': data['model'], 'configured': bool(data['api_key'])}
                for role, data in value.items()}

    def openwebui_environment(self) -> dict[str, str]:
        """只向指定原生子进程传递，调用方不得打印整个返回值。"""
        value = self.read()
        chat, embedding = value['chat'], value['embedding']
        return {'ENABLE_OPENAI_API': 'True', 'ENABLE_OLLAMA_API': 'False',
                'OPENAI_API_BASE_URL': chat['base_url'], 'OPENAI_API_BASE_URLS': chat['base_url'],
                'OPENAI_API_KEY': chat['api_key'], 'OPENAI_API_KEYS': chat['api_key'],
                'OPENAI_API_CONFIGS': json.dumps({'0': {'enable': True, 'model_ids': [chat['model']],
                    'headers': {'User-Agent': 'manufacturing-agent-platform/0.1 OpenWebUI',
                                'x-opencode-session': 'owui-{{USER_ID}}-{{CHAT_ID}}'}}}),
                'DEFAULT_MODELS': chat['model'], 'RAG_EMBEDDING_ENGINE': 'openai',
                'RAG_OPENAI_API_BASE_URL': embedding['base_url'], 'RAG_OPENAI_API_KEY': embedding['api_key'],
                'RAG_EMBEDDING_MODEL': embedding['model']}
