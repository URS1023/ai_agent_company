"""Visual comparison expressions matching the actual MES quality form rules.

These are graph configuration builders, never SQL or source database writes.
Evidence: IncomingMaterialQcForm form scripts retrieved 2026-09-16.
"""
from configurations.yinhao.graph_builder import compare, expr, field, missing


SINGLE_RULES = {
    'gt': 'lte', 'gte': 'lt', 'lt': 'gte', 'lte': 'gt',
    'eq': 'ne', 'neq': 'eq', 'ne': 'eq',
}
RANGE_RULES = {
    'between_closed': ('lt', 'gt'),
    'between_open': ('lte', 'gte'),
    'between_leftOpen': ('lte', 'gt'),
    'between_rightOpen': ('lt', 'gte'),
}


def standard_conditions():
    """Return missing/invalid and violation expressions; null is never zero."""
    supported = expr('or', *[compare('eq', '判定方式', rule)
                             for rule in [*SINGLE_RULES, *RANGE_RULES]])
    is_range = expr('or', *[compare('eq', '判定方式', rule) for rule in RANGE_RULES])
    empty_open_range = expr('and',
        expr('ne', field('判定方式'), {'value': 'between_closed'}),
        expr('eq', field('下限数值'), field('上限数值')))
    invalid = expr('or', missing('测量数值', '上限数值'), expr('not', supported),
        expr('and', is_range, expr('or', missing('下限数值'),
            expr('gt', field('下限数值'), field('上限数值')), empty_open_range)))
    violations = [expr('and', compare('eq', '判定方式', rule),
                       expr(op, field('测量数值'), field('上限数值')))
                  for rule, op in SINGLE_RULES.items()]
    violations += [expr('and', compare('eq', '判定方式', rule),
                       expr('or', expr(lower, field('测量数值'), field('下限数值')),
                            expr(upper, field('测量数值'), field('上限数值'))))
                   for rule, (lower, upper) in RANGE_RULES.items()]
    return invalid, expr('or', *violations)
