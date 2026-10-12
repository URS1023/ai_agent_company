"""Attach table records to the exact upstream composite key, retaining missing rows.

中文补充：本模块实现"读取值"节点的关联读取（match_mode 不为 off 时）：
先用所选表字段与上游字段的对应关系建立索引，再把每条上游记录与匹配到的表记录展开合并。
设计约束：
- 只做内存内的等值关联，绝不改写上游记录，也不修改源表数据。
- 关联不上的上游记录也要保留（用空值 + 匹配计数 0 表示），不能让"关联不到"静默变成"无记录"。
- 结果行数达到 ``max_rows`` 立即报错，不允许把截断后的部分记录交给后续判定。
"""
from collections import defaultdict
from app.errors import ValidationError


def input_rows(inputs):
    """取出上游记录数组；兼容"单对象"与"结果包（dict 带 data）"两种形态。

    输入不是记录数组时抛 ValidationError（提示需要连接上游表格数据），
    不做隐式包装，避免把标量当成一行记录参与关联。
    """
    value = inputs.get("input")
    value = value.get("data", value) if isinstance(value, dict) else value
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValidationError("关联读取需要连接上游表格数据")
    return value


def key(row, fields):
    """按给定字段构造关联键；任一字段缺失时返回 None 表示"不参与关联"。

    返回 Python 元组，元素统一转成字符串，使数字与文本形式的同一标识可以匹配。
    嵌套对象/数组字段无法安全比较，抛 ValidationError 要求用户改选单值字段。
    """
    # Null never joins null: missing identifiers cannot borrow unrelated data.
    values = [row.get(field) for field in fields]
    if any(v is None or v == "" for v in values):
        return None
    if any(isinstance(v, (dict, list)) for v in values):
        raise ValidationError("关联字段必须是单个文本或数字")
    return tuple(str(v) for v in values)


def attach_rows(config, inputs, fetched):
    """把已读取的表记录按配置关联到每条上游记录，返回展开后的结果行。

    参数：
        config：``ReadValuesConfig``，使用 matches / fields / field_aliases /
            keep_input_fields / match_count_field / match_mode / max_rows。
        inputs：节点输入，``input`` 为上游记录数组。
        fetched：本节点刚读出的表记录（未加别名）。

    返回：上游字段（或 keep_input_fields 指定的子集）+ 别名后的表字段 + 匹配数量字段。

    行为约定：
        match_mode="all" 展开全部匹配行；"latest" 只取第一条；"unique" 只在恰好匹配
        1 条时保留，否则视为未匹配（避免用"多条里随便挑一条"做业务判定）。
        未匹配时输出一行空表字段，保证"关联不到"这件事本身可见。

    异常：上游缺字段、输出名与上游字段重名、结果超过 max_rows 时抛 ValidationError；
    任一情况都中止读取而不是返回部分结果。
    """
    source_fields = [m.field for m in config.matches]
    upstream_fields = [m.input_field for m in config.matches]
    index = defaultdict(list)
    for row in fetched:
        value = key(row, source_fields)
        if value is not None:
            index[value].append(row)
    result = []
    names = [config.field_aliases.get(f, f) for f in config.fields]
    for original in input_rows(inputs):
        if set(upstream_fields) - original.keys():
            raise ValidationError("上游结果缺少配置的关联字段，请核对字段选择")
        retained = {f: original.get(f) for f in config.keep_input_fields} if config.keep_input_fields else original
        if (set(names) | {config.match_count_field}) & retained.keys():
            raise ValidationError("关联读取的输出名称与上游字段重复，请修改输出名称，避免覆盖原数据")
        matches = index.get(key(original, upstream_fields), [])
        count = len(matches)
        if config.match_mode == "latest":
            matches = matches[:1]
        elif config.match_mode == "unique" and count != 1:
            matches = []
        for row in matches or [{}]:
            result.append({**retained, **{config.field_aliases.get(f, f): row.get(f) for f in config.fields},
                           config.match_count_field: count})
            if len(result) > config.max_rows:
                raise ValidationError("关联结果超过读取上限，请缩小范围；未将部分记录交给后续判断")
    return result
