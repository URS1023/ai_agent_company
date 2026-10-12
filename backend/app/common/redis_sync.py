# -*- coding: utf-8 -*-
"""Sync Redis client used from sync DB sessions / background worker.

同步代码路径（同步 SQLAlchemy 会话、后台线程、worker）专用的 Redis 客户端。

系统位置：异步请求路径请用 ``common/events.get_redis()``；本模块是模块级单例（首次调用懒建连），
给不便 await 的同步调用方复用，避免每次发布都重新建连。

约束与降级：单例意味着连接随进程存活（不做健康检查、不自动重连）；``redis_publish`` 把
序列化与发布异常一律静默忽略——它只是"尽力而为"的推送，**不能当作可靠投递**，需要不丢事件
请走持久化通道（``run_events``）。异步事件循环中不要调用本模块（同步 IO 会阻塞整个循环）。
"""
from __future__ import annotations

import json

import redis
from ..config import settings

_client = None


def redis_client_sync():
    """返回模块级同步 Redis 客户端单例（首次调用时按 ``settings.REDIS_URL`` 创建）。

    redis-py 是懒连接：此处不会因 Redis 不可用而抛错，失败会在第一次发命令时体现
    （通常被 ``redis_publish`` 吞掉）。``decode_responses=True`` 使返回值为 str。
    单例在进程生命周期内复用，不要在调用方再套一层缓存，也不要在这里切换数据库编号。
    """
    global _client
    if _client is None:
        # RESP2 keeps redis-py 8 compatible with deployed Redis 5 servers.
        _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, protocol=2)
    return _client


def redis_publish(channel: str, data: dict) -> None:
    """把 dict 序列化后发布到频道（``ensure_ascii=False``，中文按原样输出便于排查）。

    参数：``channel`` 频道名（Run 事件请用 ``events.event_channel()`` 生成）；``data`` 需可 JSON 序列化。
    返回：None——**没有成功/失败返回值**；序列化或发布异常都被静默忽略（无日志、无重试，见 GOV-016），
    因此调用方无法据此判断是否送达，需要可靠送达时改用持久化路径。
    """
    try:
        redis_client_sync().publish(channel, json.dumps(data, ensure_ascii=False))
    except Exception:
        pass
