"""离线校验增量取数的 SQL 契约（子表变更要能重新选中父单据）。

验证行为：以父表为锚的增量窗口查询会同时覆盖测量/统计等子表的新增与修改时间，并带窗口边界参数。
隔离方式：只调用 quality.incremental.changed_documents_query 拼接 SQL 文本做断言，不执行 SQL。
边界：所有 TABLES 中的子表都必须出现在查询里；窗口参数名必须与调用方一致。
"""
from app.modules.quality.incremental import changed_documents_query, TABLES


def test_child_updates_including_measurement_and_statistics_reselect_parent_document():
    query = changed_documents_query('SELECT f.autoId FROM t_incomingmaterialqcform f WHERE f.autoId > :afterKey ORDER BY f.autoId ASC LIMIT 20')
    assert all(table in query for table in TABLES)
    assert 'modifiedOn' in query and 'createdOn' in query
    assert ':windowStart' in query and '< :windowEnd' in query
    assert query.endswith('ORDER BY f.autoId ASC LIMIT 20')

