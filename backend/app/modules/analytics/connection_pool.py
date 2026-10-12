"""Bounded, process-local read pools and serial workflow read batches."""
from __future__ import annotations

import atexit
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock, get_ident
import time

from app.errors import ToolExecutionError

# 池容量与复用边界（重要约束）：
#   POOL_SIZE 是**每个数据源每个配置版本**的最大连接数，而不是全局上限；
#   MAX_POOLS 是进程内池对象的总数上限，达到上限时按最久未使用回收空闲池，
#   全部在用则直接报错而不会静默扩容；
#   IDLE_SECONDS 决定空闲池的淘汰时间，避免长尾数据源长期占用连接句柄。
# 池键为 (workspace_id, source_id, 配置指纹)，因此跨工作区、跨数据源、以及同一数据源
# 的旧配置版本都不会复用同一个池——配置或凭据变更时旧池被标记 retired，无法再借出。
POOL_SIZE = 2
MAX_POOLS = 16
IDLE_SECONDS = 60


@dataclass
class PoolEntry:
    """池表中的一条记录：引擎、最近使用时间、当前借用者数量与是否已退役。

    ``users`` 是“还有多少调用方正在使用这个池”的租约计数，只有降到 0 且引擎
    自身没有 checkedout 连接时才允许销毁；``retired`` 表示配置已变更或数据源被
    删除，用于阻止新借出但允许在用查询跑完。
    """

    engine: object
    last_used: float
    users: int = 0
    retired: bool = False
    version: int | None = None


@dataclass
class SourceGeneration:
    """独立于引擎寿命保存版本下限和退役指纹，回收空池不能恢复旧配置。"""

    version: int = 0
    current_key: tuple | None = None
    retired_keys: set = field(default_factory=set)


_lock = RLock()
_pools: dict[tuple, PoolEntry] = {}
_generations: dict[tuple, SourceGeneration] = {}


def _idle(entry):
    """空闲判定必须同时满足“无借用者”和“引擎无签出连接”，否则会杀掉在用连接。"""
    return not entry.users and entry.engine.pool.checkedout() == 0


def _remove(key):
    """从池表移除并释放引擎；调用方必须持有 _lock。"""
    _pools.pop(key).engine.dispose()


def reap_idle_pools():
    """No database queries: dispose unused pools on access or maintenance ticks."""
    with _lock:
        now = time.monotonic()
        for key, entry in list(_pools.items()):
            if _idle(entry) and (entry.retired or now - entry.last_used >= IDLE_SECONDS):
                _remove(key)


@contextmanager
def pooled_engine(key, factory, *, version: int | None = None):
    """借出一个可复用的引擎，并在退出时归还借用者计数。

    参数 ``key`` 固定为 ``(workspace_id, source_id, 配置指纹)``：前两段相同的旧键
    会被标记退役并尽量回收，保证改了主机/账号/密码以后不会借到上一代连接（凭据
    与配置变更必须立刻生效，不能靠连接超时兜底）。``factory`` 仅在需要新建池时调用。

    容量与超时：单池最多 POOL_SIZE 个连接（max_overflow=0，不会临时超发）；池总数
    达 MAX_POOLS 且没有空闲池可回收时抛 ``ToolExecutionError``，宁可快速失败也不
    无限占用服务端连接。租约时间由 SQLAlchemy pool_timeout 控制（见 connections.py，
    取 min(timeout, 5) 秒），与查询语句超时是两回事：池超时只影响等待借用。

    抛出：键已退役（配置正在更新）或连接已占满时抛 ``ToolExecutionError``。
    """
    with _lock:
        reap_idle_pools()
        generation = _generations.setdefault(key[:2], SourceGeneration())
        previous = _pools.get(key)
        # 必须先拒绝迟到版本，再退役其他键；否则失败的旧请求也会误伤当前池。
        if ((version is not None and version < generation.version)
                or key in generation.retired_keys or (previous is not None and previous.retired)):
            raise ToolExecutionError("数据源连接配置已更新，请重新执行查询")
        if version is not None and version > generation.version:
            generation.version = version
            generation.retired_keys.clear()
        if generation.current_key is not None and generation.current_key != key:
            generation.retired_keys.add(generation.current_key)
        generation.current_key = key
        for old_key, entry in list(_pools.items()):
            if old_key[:2] == key[:2] and old_key != key:
                entry.retired = True
                if _idle(entry):
                    _remove(old_key)
        entry = _pools.get(key)
        if entry is not None and entry.retired:
            raise ToolExecutionError("数据源连接配置正在更新，请重新执行查询")
        if entry is None:
            if len(_pools) >= MAX_POOLS:
                idle = [(entry.last_used, old_key) for old_key, entry in _pools.items() if _idle(entry)]
                if not idle:
                    raise ToolExecutionError("可用数据连接已占满，请稍后执行查询")
                _remove(min(idle)[1])
            entry = PoolEntry(factory(), time.monotonic(), version=version)
            _pools[key] = entry
        entry.users += 1
    try:
        yield entry.engine
    finally:
        with _lock:
            entry.users -= 1
            entry.last_used = time.monotonic()
            reap_idle_pools()


def dispose_source_pools(workspace_id, source_id, *, minimum_version: int | None = None):
    """记录编辑后的版本下限；尚未创建或已回收的旧池也不能被迟到任务重建。"""
    with _lock:
        source_key = (workspace_id, source_id)
        generation = _generations.setdefault(source_key, SourceGeneration())
        if minimum_version is not None:
            # 保存回执可能晚到；v2 的清理不得退役已经被 v3 请求建立的连接池。
            if minimum_version > generation.version:
                generation.version = minimum_version
                generation.retired_keys.clear()
                generation.current_key = None
        elif generation.current_key is not None:
            generation.retired_keys.add(generation.current_key)
            generation.current_key = None
        for key, entry in list(_pools.items()):
            if key[:2] == source_key and (minimum_version is None or entry.version is None
                                         or entry.version < minimum_version):
                generation.retired_keys.add(key)
                entry.retired = True
                if _idle(entry):
                    _remove(key)


def dispose_read_pools():
    """进程退出或全局重置时退役所有池；在用连接由最后一个借用者归还时关闭。"""
    with _lock:
        for key, entry in list(_pools.items()):
            entry.retired = True
            if _idle(entry):
                _remove(key)
        if not _pools:
            # 全局关闭/隔离测试重置是注册表的新生命周期，普通空闲回收不清除此屏障。
            _generations.clear()


def pool_status(source):
    """汇报某个数据源当前**进程内**的连接占用，用于资源策略页面。

    只统计与当前工作区/数据源匹配的池：连接池是进程级对象，跨工作区不会互相复用。
    MySQL/Postgres 走 QueuePool，其余（SQLite/Elasticsearch 等）为 NullPool，
    因此 ``poolSize`` 对前者才是真实容量，后者恒为 0——不要把它当作并发上限，
    真正的并发上限来自治理查询租约（见 governance.acquire_query_lease）。
    """
    pooled = source.provider in {"mysql", "postgres"}
    with _lock:
        reap_idle_pools()
        pools = [entry.engine.pool for key, entry in _pools.items()
                 if key[:2] == (source.workspace_id, source.id)]
        idle = sum(pool.checkedin() for pool in pools)
        borrowed = sum(pool.checkedout() for pool in pools)
    return {"poolType": "QueuePool" if pooled else "NullPool", "poolScope": "process",
            "poolSize": POOL_SIZE if pooled else 0, "maxOverflow": 0,
            "persistentConnections": idle + borrowed, "idleConnections": idle,
            "borrowedConnections": borrowed}


def _owner():
    """返回当前执行主体（线程 id + 事件循环任务）。

    连接与线程/任务绑定，用它判断上下文是否被复制到了别的线程或任务：复制出来的
    上下文不能共享同一个数据库连接，否则会出现跨线程并发使用同一 session。
    """
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return get_ident(), task


class ReadBatch:
    """一次工作流/一次请求内复用的单条连接，按所属主体（线程或任务）隔离。

    生命周期：由 ``read_batch()`` 建立并关闭；同一批次的多次读操作共享一条连接，
    以减少建连开销。不变式：换引擎（切换到另一个数据源）时必须先关闭旧连接；
    出错时必须整体丢弃连接，不能把出错的会话留给下一个节点复用。
    """

    def __init__(self):
        self.owner = _owner()
        self.engine = self.connection = None

    def close(self):
        """关闭并清空连接；关闭后仍可再次 borrow（会重新建立连接）。"""
        connection, self.connection = self.connection, None
        self.engine = None
        if connection is not None:
            try:
                connection.close()
            finally:
                reap_idle_pools()

    def borrow(self, engine):
        """取得本批次在该引擎上的连接；引擎不同则先关闭旧连接再新建。"""
        if self.engine is not engine:
            self.close()
            self.connection = engine.connect()
            self.engine = engine
        return self.connection


_batch: ContextVar[ReadBatch | None] = ContextVar("analytics_read_batch", default=None)


def current_read_batch():
    """返回当前上下文可用的批次；主体不一致时返回 ``None`` 表示不可复用。

    上下文会被复制到线程/子任务（例如并发报表组件），此时必须视为“没有批次”，
    让调用方新建连接，绝不能跨线程共享连接。
    """
    batch = _batch.get()
    # Context propagation to worker threads/tasks must not share a DB connection.
    return batch if batch is not None and batch.owner == _owner() else None


def release_read_batch():
    """主动释放当前批次的连接；用于“马上要等待模型/外部服务”之前让出连接。"""
    batch = current_read_batch()
    if batch is not None:
        batch.close()


@contextmanager
def read_batch():
    """Nested workflows release the parent's borrowed connection before starting."""

    # 建立一个新的批处理上下文；进入前会先释放父级批次，避免嵌套工作流同时持有两条
    # 连接（池容量只有 2，父级不放会让子流程直接饿死）。
    release_read_batch()
    batch = ReadBatch()
    token = _batch.set(batch)
    try:
        yield batch
    finally:
        try:
            batch.close()
        finally:
            _batch.reset(token)


@contextmanager
def borrow_read_connection(engine, *, reusable):
    """借一条读连接：``reusable=True`` 时优先复用当前批次，否则每次新建并即用即还。

    ``reusable=False``（SQLite、临时凭据、非活跃数据源等）会先释放批次再使用独立
    连接，保证这类数据源不会把连接挂在批次里跨步骤存活，也避免短连接实例被复用。
    """
    batch = current_read_batch() if reusable else None
    if batch is None:
        release_read_batch()
        with engine.connect() as connection:
            yield connection
    else:
        connection = batch.borrow(engine)
        try:
            yield connection
        except BaseException:
            # Never replay a failed statement or keep its session for the next node.
            batch.close()
            raise


atexit.register(dispose_read_pools)
