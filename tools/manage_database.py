"""核验并升级平台自有本地SQLite；禁止从此工具连接外部或旧业务库。"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services' / 'platform'))

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from agent_platform.core.database import Base, Database
from agent_platform.core.settings import Settings
from agent_platform.registry import load_modules


def config(connection):
    """共享显式连接给Alembic，不把连接串写入配置或控制台。"""
    value = Config(str(ROOT / 'services/platform/alembic.ini'))
    value.attributes['connection'] = connection
    return value


def manage(action: str, settings: Settings) -> dict:
    url = make_url(settings.database_url)
    if url.get_backend_name() != 'sqlite' or not url.database or url.database == ':memory:':
        raise RuntimeError('此工具只操作平台自有本地SQLite文件；不连接外部数据库')
    target = Path(url.database).resolve()
    if not target.is_relative_to(settings.data_dir) or target.suffix != '.sqlite3':
        raise RuntimeError('数据库必须位于本项目AGENT_DATA_DIR内并以.sqlite3结尾')
    if not target.exists() and action != 'upgrade':
        raise RuntimeError('平台数据库尚未创建，请运行db:upgrade初始化')
    load_modules(())
    database = Database(settings)
    try:
        with database.engine.begin() as connection:
            cfg = config(connection)
            head = ScriptDirectory.from_config(cfg).get_current_head()
            current = MigrationContext.configure(connection).get_current_revision()
            tables = set(inspect(connection).get_table_names()) - {'alembic_version'}
            if action == 'adopt':
                if current:
                    raise RuntimeError('已存在迁移版本，不能重复接管')
                if not tables:
                    raise RuntimeError('空库请使用upgrade')
                # 旧开发库只有结构与初始快照完全相符时才允许登记版本；不猜测缺列。
                from alembic.autogenerate import compare_metadata
                differences = compare_metadata(MigrationContext.configure(connection, opts={'compare_type': True}), Base.metadata)
                if differences:
                    raise RuntimeError('开发库结构与当前模型不同，请先备份并人工编写迁移')
                command.stamp(cfg, head)
            elif action == 'upgrade':
                if tables and current is None:
                    raise RuntimeError('已有未登记版本的开发库；先备份，再运行adopt核验接管，禁止覆盖')
                command.upgrade(cfg, 'head')
            elif action != 'check':
                raise RuntimeError('不支持的数据库操作')
            actual = MigrationContext.configure(connection).get_current_revision()
            result = {'target': str(target), 'owner': '当前项目平台进程', 'local': True,
                      'revision': actual, 'head': head, 'ready': actual == head}
            if action == 'check' and not result['ready']:
                raise RuntimeError('数据库版本落后，请先备份并运行db:upgrade')
            return result
    finally:
        database.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'upgrade', 'adopt'))
    args = parser.parse_args()
    try:
        print(json.dumps(manage(args.action, Settings.from_env()), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
