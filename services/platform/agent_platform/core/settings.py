"""显式平台配置；不读取参考项目环境文件或自动复用其连接。"""

from dataclasses import dataclass, field
import os
from pathlib import Path

from fastapi import Request

PROJECT_ROOT = Path(__file__).resolve().parents[4]


@dataclass(slots=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / '.local' / 'platform')
    database_url: str = ''
    environment: str = 'development'
    allow_remote_connections: bool = False
    local_mysql_proof: str = ''
    session_hours: int = 12
    max_upload_bytes: int = 20 * 1024 * 1024
    allowed_origins: tuple[str, ...] = (
        'http://127.0.0.1:8080', 'http://localhost:8080',
        'http://127.0.0.1:5173', 'http://localhost:5173',
    )
    enabled_modules: tuple[str, ...] = ()
    # 客户端签名断言必须匹配管理员配置的接收方，不能信任请求Host或代理头。
    client_assertion_audience: str = 'http://127.0.0.1:8080/api/v1/client-auth/token'

    def __post_init__(self):
        self.data_dir = Path(self.data_dir).resolve()
        if not self.database_url:
            self.database_url = 'sqlite:///' + (self.data_dir / 'platform.sqlite3').as_posix()

    @property
    def files_dir(self) -> Path:
        return self.data_dir / 'files'

    @property
    def secure_cookies(self) -> bool:
        return self.environment == 'production'

    @classmethod
    def from_env(cls) -> 'Settings':
        defaults = cls()
        return cls(
            data_dir=Path(os.environ.get('AGENT_DATA_DIR', str(defaults.data_dir))),
            database_url=os.environ.get('AGENT_DATABASE_URL', ''),
            environment=os.environ.get('AGENT_ENV', 'development'),
            allow_remote_connections=os.environ.get('AGENT_ALLOW_REMOTE_CONNECTIONS', '').lower() == 'true',
            local_mysql_proof=os.environ.get('AGENT_LOCAL_MYSQL_PROOF', ''),
            allowed_origins=tuple(x.strip() for x in os.environ.get(
                'AGENT_ALLOWED_ORIGINS', ','.join(defaults.allowed_origins)).split(',') if x.strip()),
            enabled_modules=tuple(x.strip() for x in os.environ.get('AGENT_ENABLED_MODULES', '').split(',') if x.strip()),
            client_assertion_audience=os.environ.get(
                'AGENT_CLIENT_ASSERTION_AUDIENCE', defaults.client_assertion_audience
            ).strip(),
        )


def get_settings(request: Request) -> Settings:
    return request.app.state.settings
