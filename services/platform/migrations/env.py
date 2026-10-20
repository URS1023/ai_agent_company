"""迁移只使用调用方显式传入的连接，不读取旧系统的环境文件。"""
from alembic import context
from agent_platform.core.database import Base
from agent_platform.registry import load_modules

load_modules(())
target_metadata = Base.metadata

if context.is_offline_mode():
    raise RuntimeError('请通过项目数据库工具传入经过核验的本地连接')
connection = context.config.attributes.get('connection')
if connection is None:
    raise RuntimeError('缺少显式数据库连接，请使用 tools/manage_database.py')
context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                  render_as_batch=connection.dialect.name == 'sqlite')
with context.begin_transaction():
    context.run_migrations()
