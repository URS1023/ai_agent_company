# 把工具返回的"需要用户补充选择"结果，转成安全、可点击的业务问句（模块 docstring 见下）。
#
# 位置（历史）：工具层用 ToolResult(status=failed, error_code=validation_error,
# metadata.requires_user_input=True, data.clarification) 表达"要问用户"；
# 原由 `agent.agent_run`（休眠，见其头注释）调 `question_card` 生成 message.question
# 事件与 parts；`chat/service.message_out` 调 `public_question_parts` 在读取历史消息时补齐卡片。
#
# 为什么需要过滤（安全边界）：clarification 由模型/工具生成，可能夹带 SQL、表名、
# 字段名、堆栈、路径、内部 URL 等实现细节。本模块只放行"独立可懂且不泄露实现"的问句；
# 未通过的原始诊断仍留在 run trace 里供排障，但不进用户可见消息。
"""Safe business questions shared by stream and saved conversation projection."""
from copy import deepcopy
import re
from app.providers.tool.base import ToolResult

def is_generic_recovery_question(message: str) -> bool:
    """A failed operation is not a missing business decision."""
    text = str(message or "")
    return bool(re.search(r"继续(?:处理|查询).{0,30}还是.{0,15}(?:查看|看).{0,8}结果", text))


def customer_business_question(result: ToolResult) -> str:
    """Extract a business question only from a structured clarification result."""
    if (
        result.status != "failed"
        or result.error_code != "validation_error"
        or result.metadata.get("requires_user_input") is not True
    ):
        return ""
    data = result.data if isinstance(result.data, dict) else {}
    clarification = data.get("clarification")
    if not isinstance(clarification, str) or is_generic_recovery_question(clarification):
        return ""
    technical = re.compile(
        # 命中即视为"实现细节"，整句丢弃：SQL/表名字段名/内部路径/URL/长十六进制串，
        # 以及"请提供表名/请联系管理员"这类把排障推给用户的话术。
        r"sql|json|schema|metadata|traceback|exception|select|request.?key|session.?id|"
        r"\b[a-z]+_[a-z_]+\b|[a-z0-9]{20,}|https?://|[a-z]:[\\/]|"
        r"数据库|元数据|字段|表名|表结构|数据表|业务表|相关.{0,3}表|数据源|"
        r"函数|幂等|堆栈|服务端|没有找到|未找到|请提供|请明确|请补充|需要补充|管理员",
        re.I,
    )
    # Do not expose a technical explanation that happens to precede a valid
    # question. Only independently understandable, bounded question sentences
    # are eligible; raw diagnostics stay in the tool-call trace.
    for sentence in re.findall(r"[^。！？?!\n]+[？?]", clarification[:1000]):
        question = sentence.strip().lstrip("-•* ")
        if (
            4 <= len(question) <= 240
            and not technical.search(question)
            and re.search(r"[\u4e00-\u9fff]", question)
            and re.search(r"哪|还是|是否|吗|么|多少|几|如何|怎样|能否", question)
        ):
            return question
    return ""



def question_card(result):
    """把"需要用户选择"的工具结果转成可点击的选择卡片（不满足条件时返回 None）。

    参数：`result` 为工具返回的 ToolResult；只有 status=failed 且 error_code=validation_error
    且 metadata.requires_user_input 为 True 才会被接受（见 customer_business_question）。
    返回：{'type': 'business_question', 'question': str, 'options': [str, ...]}，
    options 最多 4 个，且每个候选值都经过同一套安全校验（用 probe 反向验证）后才会保留，
    避免把技术措辞混进按钮。无有效问句时返回 None，调用方应退化为普通文本回答。
    只读，无副作用。
    """
    question = customer_business_question(result)
    if not question:
        return None
    data = result.data if isinstance(result.data, dict) else {}
    choices = data.get('options')
    if not isinstance(choices, list):
        choices = []
    # Compatibility for existing models returning only a question string.
    # 兼容只给问句、不给结构化 options 的历史模型：从"…还是…"里切出候选。
    if not choices and '还是' in question:
        choices = re.split(r'[，,]?\s*还是', question.rstrip('？?。 '))
        choices = [re.sub(r'^(?:你|您)?(?:是)?(?:想|希望|需要)?(?:的是|是)?(?:查看|查询|看)?', '', c).strip() for c in choices]
    safe = []
    for choice in choices[:6]:
        if not isinstance(choice, str):
            continue
        value = choice.strip().rstrip('？?。')
        if not 2 <= len(value) <= 100 or value in safe:
            continue
        # 用"把候选项套进问句再走一次安全校验"的方式复检，确保按钮文案同样不含实现细节。
        probe = ToolResult(status='failed', error_code='validation_error', metadata={'requires_user_input': True},
                           data={'clarification': '你想查看' + value + '吗？'})
        if customer_business_question(probe):
            safe.append(value)
    return {'type': 'business_question', 'question': question, 'options': safe[:4]}


# 把"公开可说的失败提示"转成选择按钮：真实错误（含技术细节）保留在 tool_result 里，
# 只有通过安全校验的部分才会变成按钮，二者互不覆盖。
def recovery_question(message):
    """Offer buttons for a public recovery question while retaining the real error."""
    return question_card(ToolResult(status='failed', error_code='validation_error',
        metadata={'requires_user_input': True}, data={'clarification': message}))


# 读取期投影：老数据里只有"等待你的选择"这样的纯文本，没有结构化卡片。
# 这里在返回给前端前临时升级成可点击卡片（deepcopy，绝不回写 parts_json），
# 使历史会话与新会话有一致的交互；同时剔除已被判定为"通用失败提示"的旧卡片。
def public_question_parts(parts):
    """Upgrade old text-only waiting messages on read; never change stored history."""
    parts = [p for p in deepcopy(parts or [])
             if not (p.get("type") == "business_question"
                     and is_generic_recovery_question(p.get("question")))]
    if any(p.get('type') == 'business_question' for p in parts):
        return parts
    waiting = [p for p in parts if p.get('type') == 'tool_result'
               and p.get('result', {}).get('error_summary') == '等待你的选择']
    if not waiting:
        for p in parts:
            if p.get('type') == 'tool_result' and p.get('result', {}).get('status') == 'failed':
                card = recovery_question(p['result'].get('error_summary') or '')
                if card:
                    parts.append(card)
                    break
        return parts
    text = '\n'.join(str(p.get('text') or '') for p in parts if p.get('type') == 'text')
    card = question_card(ToolResult(status='failed', error_code='validation_error',
        metadata={'requires_user_input': True}, data={'clarification': text}))
    if card:
        for p in waiting:
            p['result'] = {'status': 'waiting_input', 'summary': '等待你的选择'}
        parts.append(card)
    return parts
