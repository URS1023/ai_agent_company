"""向量响应的纯校验与稳定几何计算，不读取配置、不请求模型。"""

from __future__ import annotations

import math

from .errors import DomainError

MAX_VECTOR_DIMENSION = 65_536


def _invalid(message: str) -> DomainError:
    return DomainError(502, 'embedding_invalid_response', message)


def validate_vectors(vectors, expected_count: int, *, expected_dimension: int | None = None):
    """验证条数和跨批固定维度；JSON数值才可入库，不能将布尔/字符串悄悄转成数值。"""
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise _invalid('向量模型返回的条数与请求不一致')
    validated, dimension = [], expected_dimension
    for vector in vectors:
        if not isinstance(vector, list) or not 1 <= len(vector) <= MAX_VECTOR_DIMENSION:
            raise _invalid('向量模型返回的向量为空或维度超限')
        dimension = len(vector) if dimension is None else dimension
        if len(vector) != dimension:
            raise _invalid('向量模型返回的维度不一致')
        values = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _invalid('向量模型返回了非数值坐标')
            try:
                value = float(value)
            except (OverflowError, ValueError) as exc:
                raise _invalid('向量模型返回的数值超出范围') from exc
            if not math.isfinite(value):
                raise _invalid('向量模型返回了非有限数值')
            values.append(value)
        if not any(values):
            raise _invalid('向量模型返回了零向量')
        validated.append(values)
    return validated, dimension


def ordered_vectors(payload, expected_count: int) -> list[list[float]]:
    """按标准index恢复输入顺序；缺号/重复/越界/省略序号都拒绝，绝不猜测对应关系。"""
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != expected_count:
        raise _invalid('向量模型返回的条数与请求不一致')
    positions = {}
    for item in data:
        if not isinstance(item, dict):
            raise _invalid('向量模型返回的记录无效')
        position = item.get('index')
        if (isinstance(position, bool) or not isinstance(position, int)
                or not 0 <= position < expected_count or position in positions):
            raise _invalid('向量模型返回的序号缺失、重复或超出范围')
        positions[position] = item.get('embedding')
    if set(positions) != set(range(expected_count)):
        raise _invalid('向量模型返回的序号不完整')
    result, _ = validate_vectors([positions[index] for index in range(expected_count)], expected_count)
    return result


def stable_cosine(left, right) -> float:
    """缩放后归一化，避免有限大数平方溢出和极小向量下溢；旧异常缓存按零相关处理。"""
    if not isinstance(left, list) or not isinstance(right, list) or not left or len(left) != len(right):
        return 0.0
    try:
        vectors, _ = validate_vectors([left, right], 2)
    except DomainError:
        return 0.0
    normalized = []
    for vector in vectors:
        scale = max(abs(value) for value in vector)
        scaled = [value / scale for value in vector]
        norm = math.sqrt(math.fsum(value * value for value in scaled))
        normalized.append([value / norm for value in scaled])
    score = math.fsum(a * b for a, b in zip(*normalized, strict=True))
    # 浮点归一化可能有末位误差，相关度仍须处于数学定义的闭区间。
    return max(-1.0, min(1.0, score))
