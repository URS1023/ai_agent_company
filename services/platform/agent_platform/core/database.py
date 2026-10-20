"""请求级事务和显式数据库初始化，业务模块通过共享Base注册表结构。"""

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .errors import DomainError
from .settings import PROJECT_ROOT, Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings):
        self.environment = settings.environment
        url = make_url(settings.database_url)
        # 开发默认只认本项目独立文件；远程生产连接必须另外显式开启。
        if url.get_backend_name() != 'sqlite' and not settings.allow_remote_connections:
            raise DomainError(503, 'database_target_not_approved', '请先配置并核验平台专用数据库')
        options: dict = {'pool_pre_ping': True}
        if url.get_backend_name() == 'sqlite':
            if url.database and url.database != ':memory:':
                Path(url.database).resolve().parent.mkdir(parents=True, exist_ok=True)
            options['connect_args'] = {'check_same_thread': False, 'timeout': 5}
            if url.database in (None, ':memory:'):
                options['poolclass'] = StaticPool
        self.engine = create_engine(settings.database_url, **options)
        if url.get_backend_name() == 'sqlite':
            @event.listens_for(self.engine, 'connect')
            def configure_sqlite(connection, _):
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('PRAGMA busy_timeout=5000')
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        if self.environment != 'production':
            # 隔离开发库可直接建表；已有生产结构只能经显式版本迁移变更。
            Base.metadata.create_all(self.engine)
            return
        from alembic.config import Config
        from alembic.migration import MigrationContext
        from alembic.script import ScriptDirectory
        config = Config(str(PROJECT_ROOT / 'services/platform/alembic.ini'))
        head = ScriptDirectory.from_config(config).get_current_head()
        with self.engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
        if not head or current != head:
            raise RuntimeError('平台数据库版本未就绪，请先备份并执行显式迁移')

    def dispose(self):
        self.engine.dispose()


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.database.sessions() as session:
        try:
            yield session
            session.commit()
        except Exception as exc:
            session.rollback()
            # 查询异常的隔离标记在新事务落库，不能把失败业务的一半一并提交。
            compensation = getattr(exc, 'after_rollback', None)
            if callable(compensation):
                with request.app.state.database.sessions.begin() as guard_session:
                    compensation(guard_session)
            raise
