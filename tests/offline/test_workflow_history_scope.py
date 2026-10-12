"""离线校验工作流历史查询的范围与工具入参契约。

验证行为：模型工具 schema 接受设备分类与历史参数；历史保留同一工作流的多个版本与多次运行；
通用总览行为不变；未匹配到设备时不静默扩大范围；显式设备分类不会默认落到质检。
隔离方式：Mock 会话 + 真实 builtin_specs/validator 契约校验，不连库、不调用模型。
边界：范围只能来自用户显式选择，缺数据时如实回报而不扩大查询。
"""
from unittest.mock import Mock
import pytest
from app.modules.workflows import business_results as reports
from app.providers.tool.builtin_provider import builtin_specs
from app.providers.tool.validator import validate_arguments


@pytest.mark.parametrize('keyword,category,count', [
    ('110kV站主变压器','inspection',5),
    ('110kV变电站主变压器','inspection',3),
    ('冶炼出炉机器人','maintenance',5),
    ('电石炉液压站','repair',5),
])
def test_model_tool_schema_accepts_device_category_and_history(keyword,category,count):
    args={'kind':'alert','keyword':keyword,'category':category,'history':True,'limit':count}
    spec=next(s for s in builtin_specs() if s.name=='query_workflow_results')
    assert validate_arguments(args,spec.input_schema,{})==args


def test_history_keeps_multiple_revisions_and_runs_of_same_workflow(monkeypatch):
    items=[{'workflowName':'110kV站主变压器 · 巡检工作流','reportUrl':'/runs/'+str(n),
        'workflowId':'main','createTime':'2026-09-17T09:00:00+00:00','revision':n,
        'historicalRevision':True,'summary':{'items':36},'data':[]} for n in [18,16]]
    listing=Mock(return_value={'kind':'alert','items':items})
    monkeypatch.setattr(reports,'list_reports',listing)
    receipt=reports.tool_result(None,{'kind':'alert','keyword':'110kV站主变压器','category':'inspection','history':True})
    args=listing.call_args.kwargs
    assert args['keyword']=='110kV站主变压器' and args['category']=='inspection'
    assert not args['latest_per_workflow'] and not args['active_only']
    assert len(receipt['items'])==2 and '最近 2 次' in receipt['message']
    assert '17:00:00（北京时间）' in receipt['message']
    assert '等待下一次定时检查' not in receipt['message']


def test_general_overview_keeps_existing_behavior(monkeypatch):
    args={'kind':'alert','reference':'','limit':5}
    assert args=={'kind':'alert','reference':'','limit':5}
    listing=Mock(return_value={'kind':'alert','items':[]})
    monkeypatch.setattr(reports,'list_reports',listing)
    reports.tool_result(None,args)
    assert listing.call_args.kwargs['latest_per_workflow'] is True


def test_unmatched_device_is_not_silently_expanded(monkeypatch):
    listing=Mock(return_value={'kind':'alert','items':[]})
    monkeypatch.setattr(reports,'list_reports',listing)
    receipt=reports.tool_result(None,{'kind':'alert','keyword':'不存在的变压器','category':'inspection','history':True})
    assert listing.call_count==1 and listing.call_args.kwargs['keyword']=='不存在的变压器'
    assert receipt['items']==[]


def test_explicit_device_category_cannot_default_to_quality(monkeypatch):
    listing=Mock(return_value={'kind':'alert','items':[]})
    monkeypatch.setattr(reports,'list_reports',listing)
    reports.tool_result(None,{'keyword':'110kV站主变压器','category':'inspection','history':True})
    assert listing.call_args.kwargs['kind']=='alert'
