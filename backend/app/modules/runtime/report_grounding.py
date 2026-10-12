# 校验"报告类回答"的断言是否有本轮工具证据支撑（模块 docstring 见下）。
#
# 位置（历史）：原由 `agent.agent_run`（休眠，见其头注释）在输出正文前调用 `report_answer_error`；
# 返回非空字符串时，该字符串会作为 system 消息回灌给模型要求重写（最多两次），仍不通过则终止本轮输出。
# 它只做"断言 vs 证据"的核对，绝不替模型挑选查询、也不构造 SQL——这是与业务查询工具的分工边界。
#
# 已知边界（为什么用规则而非模型判断）：中文表述千变万化，规则会漏判（模型换个说法就能绕过），
# 因此这里刻意只覆盖风险最高的两类错误表述（"全部正常/已解决"），宁漏不误杀；
# 判据依赖 evidenceSummary 这类显式字段，字段缺失时保守放行。
"""Validate report claims against tool evidence without choosing a query for the model."""
import json
import re

# 插入到历史消息里的标记：`_conversation_messages` 用它识别"本轮对话带过报告上下文"。
HISTORY_MARKER = '[已保存业务报告上下文]'


def report_history_note(parts, created_at):
    """把上一轮报告查询的范围摘要成一条 system 消息，附在历史助手消息之后。

    参数：`parts` 是助手消息的 parts 列表（含 tool_call），`created_at` 为消息时间。
    返回：{'role': 'system', 'content': HISTORY_MARKER + JSON} 或 None（历史里没有报告类调用时不注入）。
    为什么注入：用户说"再看一下/重新查"时，模型需要知道上次查了什么范围才能重新执行；
    但注记里必须强调"旧回答不是当前事实"，否则模型会直接复述历史结论而不调用工具。
    只读，不写库。
    """
    calls = [p for p in parts or [] if p.get('type') == 'tool_call'
             and p.get('name') in {'query_workflow_results', 'view_device_alerts'}]
    if not calls:
        return None
    return {'role': 'system', 'content': HISTORY_MARKER + json.dumps({
        'lastReportQuery': calls[-1].get('arguments') or {}, 'lastReportTool': calls[-1].get('name'),
        'observedAt': str(created_at),
        'instruction': '这是过去一次查询范围，旧助手回答不是当前事实。用户要求再看、重新查时，由你结合范围重新调用工具，不得直接复述旧结果。',
    }, ensure_ascii=False)}


def report_answer_error(answer, evidence=None):
    """检查报告类回答是否说了没证据的话，返回需要回灌给模型的纠正文案（无问题返回 ""）。

    参数：`answer` 是模型本轮正文；`evidence` 是本轮报告工具的结果 data
    （`view_device_alerts`/`query_workflow_results` 的原始返回；None 表示本轮根本没读报告）。
    返回：非空字符串即"必须重写"，其内容会作为 system 消息加进对话要求模型改正。
    判据（按风险从高到低）：
    1. 声称问题"已解决/已处置"但本轮没有 alert_problem_list 证据——处置状态必须来自问题详情，
       旧回答、流程执行完成、其他巡检报告都不算证据；
    2. 本轮没有实际读取报告却声称"已重新查询/已核实"；
    3. 声称"全部正常/全部合格"但 evidenceSummary 里 allReturnedChecksPassed 不为 True——
       没有新增记录不等于合格；
    4. 本轮存在已判定的不合格项（failedItems>0）却只字未提。
    已知边界：先按标点切句并剔除否定句（"不代表/无法/并非"等），避免把澄清句误判成断言；
    但规则匹配仍可能漏判同义改写，因此这是"降低风险"而非形式化证明。
    """
    plain = re.sub(r'[*#`]', '', answer)
    sentences = re.split(r'[。！？\n，,；;]|但是|但', plain)
    # 否定/限定语所在分句不作为正面断言（"不能说明全部正常"不应被当成"全部正常"）。
    positive = [s for s in sentences if not re.search(r'不能|不代表|不表示|无法|不等于|并非|不是|不可认定|尚未核实', s)]
    claims_clear = any(re.search(r'(?:全部|所有|均|都).{0,30}(?:正常|合格|无异常)|无异常记录|未发现.{0,12}(?:不合格|异常)|(?:体系|质检).{0,8}运行正常', s) for s in positive)
    claims_handling = any(re.search(
        r'(?:问题|故障|预警|异常)[^。\n]{0,16}(?:已(?:经)?(?:被)?(?:解决|处置|处理|消除)|(?:处置|处理)完成)|'
        r'(?:现在|目前|当前|现场)(?:问题|故障|处理|维修|处置)(?:已经|已)(?:解决|完成|恢复)|'
        r'(?:故障|液压|压力|指标)[^。\n]{0,16}(?:持续|不断|正在)(?:恶化|超标|升高)',
        s) for s in positive if not re.search(r'是否|如何|如果|怎么|怎样', s))
    if claims_handling and (not evidence or evidence.get('resultType') != 'alert_problem_list'):
        return ('本轮没有读取具体问题的当前处置记录，不能声称已解决、已处理或持续恶化。'
                '请结合用户指定问题调用 view_device_alerts，使用 problem_id 读取其真实状态和处置日志。'
                '旧助手回答、流程执行完成及其他巡检报告不等于该问题的处置证据；没有实时测量不能推断趋势。')
    if evidence is None:
        if claims_clear or re.search(r'(?:已|已经)[^。\n]{0,18}(?:查询|读取|核实|检查)|重新查询.{0,12}(?:一致|结果)', plain):
            return '本轮没有实际读取报告，不能声称已经重新查询或给出当前业务结论。请自行理解用户范围并调用报告工具，使用本轮结果回答。'
        return ''
    if evidence.get('resultType') == 'workflow_reports' and any(
        re.search(r'(?:巡检完成率|巡检覆盖率|已巡检比例).{0,20}\d+(?:\.\d+)?\s*%', s)
        for s in positive
    ):
        return ('检查项、异常项、待确认项不是设备巡检完成状态；不能用检查项减待确认项计算巡检完成率。'
                '当前报告没有完整应巡检设备分母，请展示原始检查项数量、预警与待确认情况并说明覆盖范围。')
    facts = evidence.get('evidenceSummary') or {}
    # evidenceSummary 是查询工具给出的结构化事实；缺失时视为"无法核对"，保守放行。
    if claims_clear and facts.get('allReturnedChecksPassed') is not True:
        return '本轮证据不支持全部正常或全部合格。请按 evidenceSummary 与 attentionItems 报告；无新增记录不能当作合格，有不合格必须明确披露。'
    if facts.get('failedItems', 0) > 0 and not re.search(r'不合格|异常|预警', plain):
        return '本轮存在已判定的不合格或预警项目，回答遗漏了该事实。请依据本轮报告明确数量及项目。'
    return ''
