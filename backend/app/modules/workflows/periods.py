"""Local calendar-day boundaries shared by workflow logs and monitoring.

中文补充：执行记录与监控曲线按"用户浏览器的自然日"切分，而不是按 UTC 切分，
否则跨零点的运行会被算到错误日期。因此本模块同时给出带时区的时间（用于按天分组）
和去掉时区前缀的 UTC 时间（用于直接比较数据库列，见 ``period_bounds`` 的返回顺序）。
调用方必须按下标顺序解包，不要凭名字猜顺序。
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.errors import ValidationError


def period_bounds(days: int, time_zone: str, *, now: datetime | None = None):
    """计算最近自然日的起止边界，供运行日志与监控按本地日期分桶。

    参数：
        days：统计的自然日天数（包含今天）；小于 1 时按 1 处理，保证至少覆盖当天。
        time_zone：IANA 时区名（来自浏览器），无效时抛出 ValidationError。
        now：可注入的当前时刻，仅测试使用；默认取 UTC 当前时间。

    返回四元组 ``(zone, start, end, utc_start, utc_end)``：
        zone：解析后的 ZoneInfo，供调用方把记录时间再转换成本地日期；
        start/end：本地时区的当天起点与当前时刻（带 tzinfo）；
        utc_start/utc_end：去掉 tzinfo 的 UTC naive 值，可直接与数据库列比较。

    异常：时区无效时抛 ValidationError（提示用户刷新页面重试）。
    副作用：无，纯计算。
    """
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError("统计时区无效，请刷新页面后重试") from exc
    end = (now or datetime.now(timezone.utc)).astimezone(zone)
    start_day = end.date() - timedelta(days=max(1, days) - 1)
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=zone)
    # Database timestamps are UTC without a timezone suffix.
    return zone, start, end, start.astimezone(timezone.utc).replace(tzinfo=None), end.astimezone(timezone.utc).replace(tzinfo=None)
