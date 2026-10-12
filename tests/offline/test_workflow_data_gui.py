"""Offline measurement and transaction-boundary checks for the desktop generator.

中文说明：桌面版测试数据生成器的测量值生成与事务边界检查（见 scripts/workflow_testdata）。
隔离方式：Decimal + 本地夹具，不连数据库、不启动 GUI。
边界：事务边界必须保证"要么全写入要么全回滚"，不留下半批测试数据。
"""
from datetime import datetime, timedelta
from decimal import Decimal
import random

import pytest

from scripts.workflow_testdata.database import DataError, check_destination
from scripts.workflow_testdata.generation import amount_midpoint, moment, resolve_time
from scripts.workflow_testdata.samples import OPS, outcomes, sample


@pytest.mark.parametrize('op', sorted(OPS))
@pytest.mark.parametrize('passed', [True, False])
def test_samples_really_meet_or_violate_the_standard(op, passed):
    low, high = Decimal('1.1'), Decimal('1.3')
    predicates = {'gte': lambda x: x >= high, 'gt': lambda x: x > high,
        'lte': lambda x: x <= high, 'lt': lambda x: x < high,
        'eq': lambda x: x == high, 'ne': lambda x: x != high, 'neq': lambda x: x != high,
        'between_closed': lambda x: low <= x <= high, 'between_open': lambda x: low < x < high,
        'between_leftOpen': lambda x: low < x <= high, 'between_rightOpen': lambda x: low <= x < high}
    rng = random.Random(42)
    for _ in range(10):
        value = sample({'op': op, 'low': low, 'high': high}, passed, rng)
        assert predicates[op](value) == passed
        assert value >= 0 and value.as_tuple().exponent == -6


def test_impossible_nonnegative_measurement_is_rejected():
    with pytest.raises(DataError):
        sample({'op': 'gte', 'high': 0}, False, random.Random(1))


def test_mixed_ratio_and_write_time_are_explicit():
    assert outcomes(4, 'mixed', 25, random.Random(1)).count(False) == 1
    assert all(outcomes(4, 'pass', 99, random.Random(1)))
    now = datetime(2026, 9, 17, 16)
    assert resolve_time({'createdOn': moment(), 'completed': moment(-3)}, now) == {
        'createdOn': now, 'completed': now - timedelta(seconds=3)}
    assert Decimal(amount_midpoint('0.02-0.04kg')[:-2]) == Decimal('0.03')


@pytest.mark.parametrize('host,port,database', [('192.168.1.2',3306,'szh_new'),
    ('127.0.0.1',3307,'szh_new'), ('127.0.0.1',3306,'production')])
def test_nonlocal_or_other_database_is_rejected(host, port, database):
    with pytest.raises(DataError):
        check_destination(host, port, database)
