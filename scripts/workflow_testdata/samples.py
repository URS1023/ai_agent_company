"""Generate measurements on a requested side of the configured numeric boundary."""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .database import DataError

UNIT = Decimal('0.000001')
OPS = {'gte', 'gt', 'lte', 'lt', 'eq', 'ne', 'neq', 'between_closed', 'between_open',
       'between_leftOpen', 'between_rightOpen'}


def decimal(value):
    """把配置里的数值（可能是字符串）转成 Decimal；非法或非有限值时抛 DataError。"""
    try:
        result = Decimal(str(value))
        if result.is_finite():
            return result
    except Exception:
        pass
    raise DataError('检查标准缺少有效数值')


def sample(rule, passed, rng):
    """按标准边界生成一条「必定通过」或「必定不通过」的测量值。

    参数：rule 含 op/high(/low)（来自工作流规则的比较方式与阈值），passed 决定生成在边界的哪一侧，
    rng 为随机源。严格不等式用 1e-6（UNIT）贴边，避免生成到边界上被判不同结果。
    返回：非负 Decimal（负区间会被夹到 0）；区间在非负范围内无法取到时抛 DataError。
    """
    op, high = rule['op'], decimal(rule['high'])
    if op not in OPS:
        raise DataError('检查标准的比较方式尚未支持')
    margin = max(abs(high) / 10, UNIT * 10)
    if op.startswith('between_'):
        low = decimal(rule['low'])
        if low > high:
            raise DataError('检查标准的下限高于上限')
        if passed:
            left = low + (UNIT if op in {'between_open', 'between_leftOpen'} else 0)
            right = high - (UNIT if op in {'between_open', 'between_rightOpen'} else 0)
        else:
            left, right = high + UNIT, high + margin
    elif op in {'gte', 'gt'}:
        if passed:
            left, right = high + (UNIT if op == 'gt' else 0), high + margin
        else:
            left, right = high - margin, high - (UNIT if op == 'gte' else 0)
    elif op in {'lte', 'lt'}:
        if passed:
            left, right = high - margin, high - (UNIT if op == 'lt' else 0)
        else:
            left, right = high + (UNIT if op == 'lte' else 0), high + margin
    elif (op == 'eq') == passed:
        left = right = high
    else:
        left, right = high + UNIT, high + margin
    left = max(Decimal(0), left)
    first = int((left / UNIT).to_integral_value(rounding=ROUND_CEILING))
    last = int((right / UNIT).to_integral_value(rounding=ROUND_FLOOR))
    if first > last:
        raise DataError('该标准在非负测量范围内无法生成所选结果：' + str(rule.get('name', '')))
    return Decimal(rng.randint(first, last)) * UNIT


def outcomes(count, mode, fail_percent, rng):
    """生成每条的通过/不通过标记列表（True=通过），顺序随机打散。

    mode：pass 全部通过、fail 全部不通过、mixed 按 fail_percent 计算不通过条数（四舍五入）。
    参数范围外的组合抛 DataError（调用方直接展示中文提示）。
    """
    if not 1 <= count <= 20 or mode not in {'pass', 'fail', 'mixed'} or not 0 <= fail_percent <= 100:
        raise DataError('请选择 1–20 条数据和有效的生成模式')
    fails = 0 if mode == 'pass' else count if mode == 'fail' else (count * fail_percent + 50) // 100
    values = [False] * fails + [True] * (count - fails)
    rng.shuffle(values)
    return values
