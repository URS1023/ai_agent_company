"""离线校验业务总览（overview）的范围、聚合与失败策略。

验证行为：财务/订单等主题能生成有界总览；显式子主题不扩张成全量财务数据；
应收口径排除存货收款与收款人字段但保留真实财务凭证与业务标签；
缺数据时只提示目录内的主题，且首条查询失败即停止、不调用模型。
隔离方式：SimpleNamespace + MagicMock 冒充 db 与模型，纯函数断言，不连库、不提向量索引。
边界：不能构建全库索引或抓取样本行；聚合与筛选条件不能被丢弃。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.modules.analytics import business_overview as overview, metadata, queries, semantic


def tables():
    return [{'tableName': name, 'tableComment': comment, 'columns': [
        {'columnName': 'isDeleted'}, {'columnName': 'amount'}, {'columnName': 'password'},
    ]} for name, comment in [
        ('t_caiwuchengbenbaobiao', '财务成本报表'), ('t_caiwulirunbaobiao', '财务利润报表'),
        ('t_fundpaymentplan', '资金支付计划'), ('t_saleorderproduct', '销售订单产品表'),
        ('t_saleorder', '销售订单'), ('t_caigoudingdan', '采购订单'), ('t_salecontract', '销售合同管理'),
    ]]


def test_overview_covers_real_finance_and_order_topics():
    selection = overview.overview_catalog(tables(), '查看财务和订单数据')
    selected = {r['selected']['tableName'] for r in selection if r['selected']}
    assert {'t_caiwuchengbenbaobiao', 't_caiwulirunbaobiao', 't_saleorder', 't_caigoudingdan'} <= selected
    assert 't_saleorderproduct' not in selected
    assert overview.wants_overview('查看财务和订单数据')
    assert not overview.wants_overview('统计订单总额')
    assert not overview.wants_overview('查看订单编号ABC的明细')
    assert not overview.wants_overview('查询今年的财务数据')


@pytest.mark.parametrize('question', [
    '收款与回款数据', '查看收款与回款数据', '应收数据', '付款与应付数据',
    '财务数据', '发票数据', '合同数据', '请帮我查看全部财务和订单数据',
])
def test_simple_topic_requests_use_bounded_overview(question):
    assert overview.wants_overview(question)


@pytest.mark.parametrize('question', [
    '查看客户张三的收款数据', '查看客户收款数据', '查询今年的回款数据',
    '查看本季度回款', '收款金额', '统计回款总额', '查看金额大于一千的收款',
    '收款日期', '收款明细', '查看2026年回款', '查询最近付款',
])
def test_overview_does_not_discard_filters_or_aggregations(question):
    assert not overview.wants_overview(question)


@pytest.mark.parametrize(('name', 'comment'), [
    ('t_warehousereceipt', '入库单'),
    ('t_goods_receipt', '收货记录'),
    ('t_material_receipt', '材料签收单'),
    ('t_collection', '数据集合'),
    ('t_receipt', '签收记录'),
    ('t_payment_plan', '资金支付计划'),
    ('t_supplier_payment', '供应商付款（收款单位）'),
])
def test_receivables_exclude_inventory_collections_and_payee_fields(name, comment):
    table = {'tableName': name, 'tableComment': comment, 'columns': [
        {'columnName': 'payee', 'comment': '收款单位'},
        {'columnName': 'account', 'comment': '收款账户'},
    ]}
    catalog = overview.overview_catalog([table], '收款与回款数据')
    assert [item['topic'] for item in catalog] == ['收款与回款']
    assert catalog[0]['selected'] is None
    assert catalog[0]['candidates'] == []


@pytest.mark.parametrize(('name', 'comment'), [
    ('t_cash_receipt', ''),
    ('t_paymentreceipt', ''),
    ('t_customerReceipt', ''),
    ('t_receivables', ''),
    ('t_debt_collection', ''),
    ('t_shoukuan', '收款登记'),
    ('t_huikuan', '客户回款记录'),
    ('t_yingshou', '应收账款'),
])
def test_receivables_preserve_financial_receipts_and_real_business_labels(name, comment):
    table = {'tableName': name, 'tableComment': comment, 'columns': [{'columnName': 'amount'}]}
    catalog = overview.overview_catalog([table], '收款与回款数据')
    assert catalog[0]['selected'] == table
    assert catalog[0]['candidates'] == [name]


@pytest.mark.parametrize(('question', 'expected'), [
    ('收款与回款数据', ['收款与回款']),
    ('查看财务中的收款数据', ['收款与回款']),
    ('查看付款和应付', ['付款与应付']),
    ('查询发票', ['发票']),
    ('查看财务成本', ['财务成本']),
    ('查看收款和付款', ['收款与回款', '付款与应付']),
    ('查看销售订单', ['销售订单']),
])
def test_explicit_subtopics_do_not_expand_to_all_financial_data(question, expected):
    assert [item['topic'] for item in overview.overview_catalog(tables(), question)] == expected


def test_missing_receivables_offer_only_catalogued_topics_without_queries(monkeypatch):
    snapshot = tables() + [{
        'tableName': 't_warehousereceipt', 'tableComment': '入库单',
        'columns': [{'columnName': 'amount'}],
    }]
    monkeypatch.setattr(metadata, 'source_metadata', lambda *_: SimpleNamespace(tables_json=snapshot))
    execute = MagicMock(side_effect=AssertionError('No business SQL is needed for unmatched metadata'))
    model = MagicMock(side_effect=AssertionError('No model call is needed for a catalog overview'))
    monkeypatch.setattr(queries, 'execute_query', execute)
    monkeypatch.setattr(queries.model, 'chat_for_run', model)
    source = SimpleNamespace(id='source', provider='mysql')

    result = overview.execute_overview(None, source, '收款与回款数据')

    assert result['results'] == [{
        'topic': '收款与回款', 'candidateTables': [], 'status': 'no_matching_metadata',
    }]
    assert result['availableTopics'] == ['付款与应付', '财务成本', '财务利润', '合同']
    assert result['coverage']['candidateTableCount'] == 0
    assert '当前授权目录未匹配' in result['summary']
    assert '不能断言不存在业务数据或金额为0' in result['guidance']
    assert '不能顶替收款回款' in result['guidance']
    assert '不重复查询' in result['guidance']
    assert '不要求用户提供表名或字段' in result['guidance']
    assert not execute.called and not model.called


def test_overview_bounded_no_llm_and_stops_on_first_failure(monkeypatch):
    monkeypatch.setattr(metadata, 'source_metadata', lambda *_: SimpleNamespace(tables_json=tables()))
    execute = MagicMock(return_value=SimpleNamespace(data=[{'amount': 1}], columns=[], queryId='fake'))
    monkeypatch.setattr(queries, 'execute_query', execute)
    source = SimpleNamespace(id='source', provider='mysql')
    result = overview.execute_overview(None, source, '查看财务和订单数据')
    assert 4 <= execute.call_count <= 8
    for call in execute.call_args_list:
        payload = call.args[1]
        assert payload.mode == 'sql' and payload.max_rows == 5
        assert not payload.need_analysis and not payload.need_chart
        assert 'password' not in payload.query
        assert 'LIMIT 5' in payload.query
    assert '样本行数' in result['guidance']
    execute.reset_mock()
    execute.side_effect = RuntimeError('timeout')
    with pytest.raises(RuntimeError, match='timeout'):
        overview.execute_overview(None, source, '查看财务和订单数据')
    assert execute.call_count == 1


def test_cold_recall_never_builds_full_index_or_fetches_samples(monkeypatch):
    ctx = SimpleNamespace(db=MagicMock(), workspace_id='w')
    ctx.db.scalars.return_value.all.return_value = []
    source = SimpleNamespace(id='s')
    monkeypatch.setattr(semantic, 'source_version', lambda *_: 1)
    forbidden = MagicMock(side_effect=AssertionError('Unexpected expensive operation'))
    monkeypatch.setattr(semantic, 'build_semantic_index', forbidden)
    monkeypatch.setattr(semantic.model, 'embed_texts', forbidden)
    monkeypatch.setattr(semantic, 'attach_safe_samples', forbidden)
    recalled, evidence = semantic.recall_tables(ctx, source, tables(), '财务', include_samples=False)
    assert recalled and evidence
    assert not forbidden.called


def test_ordinary_metadata_load_does_not_embed_entire_database(monkeypatch):
    ctx = SimpleNamespace(db=MagicMock(), workspace_id='w')
    ctx.db.scalar.return_value = None
    source = SimpleNamespace(id='s', provider='mysql', owner_user_id='u', connection_config={})
    monkeypatch.setattr(metadata, 'source_version', lambda *_: 1)
    monkeypatch.setattr(metadata, 'inspect_sql_source', lambda *_: tables())
    monkeypatch.setattr(metadata, 'flag_modified', lambda *_: None)
    monkeypatch.setattr(semantic, 'catalog_profile', lambda *a, **kw: {'semanticStatus': 'keyword_only'})
    build = MagicMock(return_value={'semanticStatus': 'ready'})
    monkeypatch.setattr(semantic, 'build_semantic_index', build)
    metadata.source_metadata(ctx, source)
    assert not build.called
    metadata.source_metadata(ctx, source, refresh=True)
    assert build.call_count == 1
