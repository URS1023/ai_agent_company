"""离线校验业务追问卡（clarification）的生成与投影规则。

验证行为：显式选项被保留、不安全选项被移除；旧版「水质」类问题获得可点击选项；
开放式提问允许自由作答而不编造选项；历史失败记录被投影时不被改写；
真实错误不会被伪装成追问；通用兜底问题不会反复重建。
隔离方式：以 ToolGateway 的错误结果构造真实问题对象，纯函数断言，不连库、不调用模型。
边界：真实错误与追问必须区分；已保存的失败记录只读投影。
"""
from copy import deepcopy
from app.errors import ValidationError
from app.modules.runtime.clarification import question_card, public_question_parts
from app.providers.tool.gateway import ToolGateway


def result(question, options=None):
    return ToolGateway._error_result('query_business_data', ValidationError(
        '需要补充业务口径', clarification=question, options=options))


def test_explicit_options_survive_and_unsafe_options_are_removed():
    card=question_card(result('你想查看哪类用水？', ['生产用水', '生活用水', '生产用水',
        '请提供数据库表名', '<img src=x onerror=alert(1)>']))
    assert card['options'][:2] == ['生产用水', '生活用水']
    assert '请提供数据库表名' not in card['options']


def test_legacy_water_question_gets_clickable_choices():
    question='你是想查看生产过程中的用水数据，还是其他类型的水资源消耗记录？'
    card=question_card(result(question))
    assert card['options']==['生产过程中的用水数据','其他类型的水资源消耗记录']


def test_open_question_allows_custom_response_without_invented_choices():
    card=question_card(result('你想查看哪一种水资源消耗记录？'))
    assert card and card['options']==[]


def test_legacy_history_is_projected_without_mutating_saved_failure():
    parts=[{'type':'tool_result','result':{'status':'failed','error_summary':'等待你的选择'}},
           {'type':'text','text':'你想查看所有车间的工业用水，还是特定车间的用水数据？'}]
    original=deepcopy(parts)
    projected=public_question_parts(parts)
    assert parts==original
    assert projected[0]['result']['status']=='waiting_input'
    assert projected[-1]['type']=='business_question'
    assert public_question_parts(projected)==projected


def test_real_errors_do_not_become_questions():
    parts=[{'type':'tool_result','result':{'status':'failed','error_summary':'连接超时'}},
           {'type':'text','text':'你想查看哪类数据？'}]
    assert public_question_parts(parts)==parts
    assert question_card(result('请提供数据库表名，可以吗？')) is None


def test_generic_recovery_question_is_not_recreated_and_keeps_failure():
    parts=[{'type':'tool_result','result':{'status':'failed', 'error_summary':
        '本次处理未完成，已保留已完成的结果。您想继续处理这一项，还是先查看现有结果？'}}]
    view=public_question_parts(parts)
    assert view[0]['result']['status']=='failed'
    assert view == parts
    old_card = {'type': 'business_question',
                'question': '您想继续处理这一项，还是先查看现有结果？',
                'options': ['继续处理这一项', '先查看现有结果']}
    original = deepcopy(parts + [old_card])
    assert public_question_parts(original) == parts
    assert original[-1] == old_card
    assert question_card(result(old_card['question'])) is None
