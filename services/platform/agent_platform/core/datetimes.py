"""明确以UTC写入的数据库时间转换；不用于来源未知的设备原始时间。"""
from datetime import datetime, timezone


def utc_storage_iso(value: datetime | None) -> str | None:
    """恢复SQLite丢失的UTC时区，避免浏览器将同一时刻解释为本地时间。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
