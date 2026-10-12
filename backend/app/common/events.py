# -*- coding: utf-8 -*-
"""Run event publishing: DB persistence (run_events) + Redis pub/sub for SSE.

Run 事件的双通道发布设施：``run_events`` 落库（可重放的真相来源）+ Redis pub/sub（在线推送）。

系统位置：``run_events`` 由两个消费方读取——运行详情接口（``modules/operations``）与 SSE 推流
（``modules/runtime/chat_jobs`` 实际走内存缓冲 + 轮询 ``chat.stream`` 行，不依赖 Redis）；
本模块的 Redis 分支是给"跨进程实时推送"预留/使用的通道。

降级行为（重要）：Redis 不可用时，``persist_and_publish`` / ``publish_live`` 里的发布异常被
``except Exception: pass`` **静默吞掉**（无日志、无告警，见审计项 GOV-016）。设计意图是
"实时推送是尽力而为，落库才是可靠路径"：DB 写成功即视为事件已产生，Redis 故障不会让 Run 失败，
但前端实时进度会停滞（可从 ``run_events`` 重放补齐）。因此**不要**把 Redis 发布成功当作业务前置条件。

注意：``persist_and_publish`` 会提交调用方事务（见其 docstring），使用时必须确认事务边界。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from ..db import new_session
from ..models import RunEvent


async def get_redis():
    """惰性构造一个异步 Redis 客户端（每次调用都是新连接客户端，调用方负责 ``aclose()``）。

    惰性导入 ``redis.asyncio`` 与 ``settings``，避免不需要 Redis 的进程（CLI、测试）在导入期建立连接；
    ``protocol=2``（RESP2）为兼容部署侧的 Redis 5；``decode_responses=True`` 使响应返回 str。
    这里不发命令，因此连接失败不会在此抛出，而是在真正 publish 时以异常体现
    （异步路径不要复用同步单例 ``redis_sync.redis_client_sync``）。
    """
    from redis.asyncio import Redis
    from ..config import settings
    return Redis.from_url(settings.REDIS_URL, decode_responses=True, protocol=2)


def event_channel(run_id: str) -> str:
    """返回某次 Run 的 Redis 频道名（``run.events.<run_id>``）。

    发布方与订阅方都必须经此函数取频道名（不要手写字符串），否则改名会静默失配。
    """
    return "run.events." + run_id


def persist_run_event(session, *, workspace_id: str, run_id: str, event_type: str, payload: dict, sequence: int) -> None:
    """Insert a row in run_events in the given session (caller commits).

    参数：``session`` 调用方持有的会话（**本函数不 commit**，随调用方事务一起提交/回滚）；
    ``event_type`` 事件类型字符串（如 ``run.started``、``chat.stream``）；``payload`` 事件体，
    需可 JSON 序列化；``sequence`` 同一 Run 内的单调序号，重放方按它排序，因此必须由调用方保证递增。

    返回：None。异常：不抛业务异常（ID/时间由本函数补全，落库失败由调用方在 commit/flush 时感知）。
    副作用：仅 ``session.add``。
    """
    session.add(RunEvent(
        workspace_id=workspace_id,
        run_id=run_id,
        event_type=event_type,
        payload=payload,
        created_at=datetime.utcnow(),
        sequence=sequence,
    ))


def persist_and_publish(session, *, workspace_id: str, run_id: str, event_type: str, payload: dict, sequence: int) -> None:
    """先落库并提交事件，再尽力同步推送到 Redis 频道。

    副作用（注意事务边界）：调用 ``persist_run_event`` 后**调用方 session.commit()**——本函数会
    连同调用方此前挂起的所有改动一起提交，必须在明确的提交点调用，不能塞进长度事务中间
    （否则会提交未完成的业务写）；提交失败直接上抛给调用方。

    降级：Redis 发布异常被静默忽略（无日志），已提交的事件行仍然可用，调用方不会感知 Redis 故障。
    发送的报文为 ``{"type", "run_id", "payload", "ts"}``（``ts`` 为 UTC ISO 字符串，
    ``ensure_ascii=False`` 保留中文），订阅方按同一结构解析。
    """
    from .redis_sync import redis_client_sync
    persist_run_event(session, workspace_id=workspace_id, run_id=run_id, event_type=event_type, payload=payload, sequence=sequence)
    session.commit()
    try:
        redis_client_sync().publish(
            event_channel(run_id),
            json.dumps({"type": event_type, "run_id": run_id, "payload": payload, "ts": datetime.utcnow().isoformat()}, ensure_ascii=False),
        )
    except Exception:
        pass


async def publish_live(run_id: str, event_type: str, payload: dict) -> None:
    """只推 Redis、不落库的即时通知（用于无需重放的实时提示）。

    每次调用新建异步客户端并在结束时 ``aclose()``（不复用长连接），因此高频调用会有连接开销。
    异常（含 Redis 不可用）一律吞掉，**不保证送达**；需要"客户端一定能拿到"的事件必须走落库路径。
    """
    try:
        r = await get_redis()
        await r.publish(event_channel(run_id), json.dumps(
            {"type": event_type, "run_id": run_id, "payload": payload, "ts": datetime.utcnow().isoformat()}, ensure_ascii=False))
        await r.aclose()
    except Exception:
        pass


async def replay_events(run_id: str, publisher) -> None:
    """Replay persisted run_events to an SSE publisher callback (async callable).

    参数：``run_id`` 目标 Run；``publisher`` 接收单个事件 dict 的异步可调用（如 SSE 的写入函数）。
    行为：用独立 session 按 ``sequence`` 升序读取该 Run 的全部事件并逐条 await 推送（**不分页**，
    事件很多的 Run 会一次读全）；session 在 finally 中关闭。异常：数据库异常向上抛给调用方，
    ``publisher`` 自身抛出的异常也不会被吞（由推流端处理断线）。
    注意：不订阅实时频道，只重放"读取时刻已落库"的事件；读取期间新产生的事件需要调用方
    自行衔接（通常先订阅 Redis 再重放，或用游标再次读取）。
    """
    from sqlalchemy import select
    session = new_session()
    try:
        rows = session.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)).scalars().all()
        for row in rows:
            await publisher({"type": row.event_type, "run_id": row.run_id, "payload": row.payload, "ts": row.created_at.isoformat()})
    finally:
        session.close()
