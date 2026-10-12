"""Bounded pagination after the caller has applied authorization and ordering.

有界分页工具：调用方先完成权限/数据范围过滤与排序，再由本函数套上 count + limit/offset。

系统位置：报告/采集/回写等列表接口用它把 SQLAlchemy 语句转成响应 dict，
键名与 ``common.contracts.PagedEnvelope`` 的字段一一对应（``data``/``total``/``limit``/``offset``/``hasMore``），
两侧改动必须同步。

约束：**不做任何鉴权**——statement 必须已经带上工作区与数据范围条件，否则会把越权数据分页出去；
分页参数非法抛 ``ValueError``（路由层应转成 422）；越界 ``offset`` 不报错而是回收到最后一页起点，
返回值里的 ``offset`` 是实际生效值，前端应使用它而不是自己传入的值。

副作用：只读（一次 count + 一次分页 select），不写库、不缓存（每次调用都重新计数）。
"""
from sqlalchemy import func, select


def page_records(db, statement, *, limit: int, offset: int, view) -> dict:
    """对已过滤、已排序的语句做有界分页，返回可直接进 ``Envelope`` 的 dict。

    参数：``db`` 会话（只执行查询）；``statement`` 已带 where 的 select；``limit`` 每页条数（>=1）；
    ``offset`` 起始偏移（>=0，越界会被回收）；``view`` 单行 → 视图 dict/模型的转换函数（逐行调用，
    不要在 view 里再查库，否则会触发 N+1 或懒加载报错）。

    返回：``{"data": [...], "total": int, "limit": int, "offset": int, "hasMore": bool}``；
    其中 ``hasMore`` 按"实际生效 offset + 本页行数 < total"计算，因此最后一页恒为 False。
    空结果集时 ``offset`` 归一为 0。

    异常：``limit < 1`` 或 ``offset < 0`` 抛 ``ValueError("invalid pagination")``；
    数据库异常原样上抛。副作用：无（只读）。
    """
    if limit < 1 or offset < 0:
        raise ValueError("invalid pagination")
    # 计数时去掉排序：多数数据库不接受带 ORDER BY 的 count 子查询，去掉也能省一次排序开销。
    total = int(db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
    # 越界 offset 回收到最后一页起点（total=0 时为 0），避免前端拿着过期游标得到空页而误判"没有数据"。
    offset = min(offset, max(0, (total - 1) // limit * limit))
    data = [view(row) for row in db.scalars(statement.offset(offset).limit(limit))]
    return {"data": data, "total": total, "limit": limit, "offset": offset, "hasMore": offset + len(data) < total}
