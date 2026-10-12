"""包含大段 JSON/正文的历史记录，先排序主键，再读取内容，避免数据库排序大对象。"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

Row = TypeVar("Row")


def ordered_payload_rows(
    db: Session,
    model: type[Row],
    filters: Sequence[ColumnElement[bool]],
    ordering: Sequence[ColumnElement[Any]],
    *,
    limit: int | None = None,
    batch_size: int = 500,
) -> list[Row]:
    """在原事务和过滤范围内按轻量主键排序，分批取完整记录后恢复同一顺序。

    适用于具有单列 ``id`` 主键的模型。排序阶段不选择正文或 JSON；内容阶段不带
    ORDER BY，防止 MySQL 将大对象放进 filesort 缓冲区。保留调用方的排序与限额，
    每批最多 500 个主键绑定参数；两阶段之间已删除的记录直接略过。
    """
    if not 1 <= batch_size <= 500:
        raise ValueError("历史读取批量必须在 1 到 500 之间")
    identities_query = select(model.id).where(*filters).order_by(*ordering)
    if limit is not None:
        identities_query = identities_query.limit(limit)
    identities = list(db.scalars(identities_query))
    result = []
    for start in range(0, len(identities), batch_size):
        batch = identities[start:start + batch_size]
        rows = db.scalars(select(model).where(*filters, model.id.in_(batch))).all()
        lookup = {row.id: row for row in rows}
        result.extend(lookup[identity] for identity in batch if identity in lookup)
    return result
