"""覆盖已知UTC存储的SQLite回读及有时区时间，不连接现有数据库。"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, create_engine, text

from agent_platform.core.datetimes import utc_storage_iso


def test_utc_time_survives_sqlite_timezone_loss():
    engine = create_engine('sqlite:///:memory:')
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE owned_times (created_at DATETIME)'))
            statement = text('INSERT INTO owned_times VALUES (:stamp)').bindparams(stamp=datetime(2026, 10, 20, 22, 9, tzinfo=timezone.utc))
            connection.execute(statement)
            value = connection.execute(text('SELECT created_at FROM owned_times').columns(created_at=DateTime(timezone=True))).scalar_one()
            assert utc_storage_iso(value) == '2026-10-20T22:09:00+00:00'
    finally:
        engine.dispose()


def test_existing_offset_is_converted_instead_of_relabelled():
    local = datetime(2026, 10, 21, 6, 9, tzinfo=timezone(timedelta(hours=8)))
    assert utc_storage_iso(local) == '2026-10-20T22:09:00+00:00'
    assert utc_storage_iso(None) is None
