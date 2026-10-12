# -*- coding: utf-8 -*-
"""ULID identifier helpers — DB schema uses 26-char ULID primary keys.

职责：统一生成与规范化业务主键（ULID 字符串）。

系统位置：所有模块的 models/服务层在落库前通过这里取主键，避免各自实现 ID 生成。

约束：主键长度固定 26 字符且按时间有序，数据库列宽按此设定，不可替换为 UUID 文本；
`new_id()` 不保证全局唯一冲突时的重试语义，唯一性最终由数据库约束兜底。
"""
import ulid

def new_id() -> str:
    """生成新的 26 字符 ULID 主键字符串（时间前缀有序，便于按时间排序）。"""
    return ulid.new().str

def parse_id(raw: str) -> str:
    """规范化外部传入的 ID：仅去除首尾空白。

    这里不校验格式是有意为之——历史数据与外部集成可能带空白/大小写差异，
    合法性判断交给查询与数据库；调用方不要假设返回值一定是合法 ULID。
    """
    return raw.strip()
