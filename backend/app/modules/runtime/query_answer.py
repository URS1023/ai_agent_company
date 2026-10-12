# 当模型正文不完整或不可用时，直接把本轮真实查询值交付给用户（模块 docstring 见下）。
#
# 位置（历史）：原由 `agent.agent_run`（休眠，见其头注释）在三种情形下调用 `query_values_answer`：
# 正文被判为不完整（`incomplete_answer`）、模型因余额/限流/不可用而无法生成补充说明、
# 以及查询返回空结果但必须如实呈现"0 行"时。它是"兜底交付"通道，不是常规回答路径。
#
# 边界：只接受调用方传入的**本轮**成功回执（含 queryId 且 success 非 False），
# 不读数据库、不重放查询、不推测值；空结果与 NULL 必须原样展示，
# 使"没有记录"不被误读为"记录为空值"，也不允许用模型编造的数据替代。
"""Deliver actual query values when model prose is incomplete; never invent rows."""
from __future__ import annotations

import html
import re


def incomplete_answer(payload: dict, text: str) -> bool:
    """判断模型本轮输出是否"没说完"，用于决定是否走兜底交付或重试一次。

    判据：finish_reason 不是 stop/tool_calls/function_call（供应商提前断开或触发 token 上限），
    或正文出现未闭合的代码块（``` 计数为奇数）、或以孤立 Markdown 标题/粗体标记结尾。
    参数：`payload` 为流式最终回包，`text` 为已聚合的正文。纯函数，无副作用。
    """
    # A provider can close an SSE connection without a terminal choice, or
    # report a token limit. Neither is a complete assistant/tool message.
    if 'finish_reason' in payload and payload['finish_reason'] not in {'stop', 'tool_calls', 'function_call'}:
        return True
    return text.count('```') % 2 == 1 or bool(re.search(r'(?:^|\n)\s*(?:#{1,6}|\*{1,2})\s*$', text))


# 参数：`data` 必须是本轮查询工具的返回 data（含 queryId；success 为 False 视为无效）。
# 返回：Markdown 文本；`data` 不合法时返回空串（调用方据此放弃兜底交付）。
# 呈现规则：单行单列且未被截断时直接给出"实际查询值 + 结果字段"；否则渲染最多 20 行 × 20 列的
# 表格，并显式标注总行数与"正文仅显示部分结果"。单元格做 HTML 转义并把 `|`→`&#124;`、
# 换行→`<br>`，防止真实数据破坏表格结构或注入标记。纯渲染，不写库。
def query_values_answer(data: dict | None) -> str:
    """Render only a current successful receipt supplied by the caller."""
    if not isinstance(data, dict) or not data.get('queryId') or data.get('success') is False:
        return ''
    rows = data.get('data')
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return ''
    intro = '本轮查询已执行，以下为数据库实际返回的结果。'
    if not rows:
        return intro + '\n\n当前筛选范围内未返回记录；这不代表其他范围也没有数据。'
    def cell(value):
        """把单个单元格转成安全的表格文本：NULL 显式标注、超长截断、转义表格与换行字符。"""
        # NULL 与 0/空串必须区分显示，否则会把"数据库空值"读成"零"。
        if value is None:
            return 'NULL（数据库空值）'
        text = str(value)
        if len(text) > 500:
            text = text[:500] + '…（原值较长，此处仅显示前500字符）'
        return html.escape(text).replace('|', '&#124;').replace('\n', '<br>').replace('\r', '')
    if len(rows) == 1 and len(rows[0]) == 1 and not data.get('truncated'):
        key, value = next(iter(rows[0].items()))
        return intro + f'\n\n实际查询值：{cell(value)}\n\n结果字段：{cell(key)}'
    keys = list(dict.fromkeys(key for row in rows[:20] for key in row))[:20]
    if not keys:
        return ''
    table = ['| ' + ' | '.join(cell(key) for key in keys) + ' |',
             '| ' + ' | '.join('---' for _ in keys) + ' |']
    table.extend('| ' + ' | '.join(cell(row.get(key)) for key in keys) + ' |' for row in rows[:20])
    note = f'\n\n本次返回{len(rows)}行。'
    if data.get('truncated') or len(rows) > 20 or any(len(row) > 20 for row in rows):
        note += '正文仅显示部分结果，不能将其作为全量统计。'
    return intro + '\n\n' + '\n'.join(table) + note
