"""精提Test5 analytics/statistics.py的Decimal转换、精度预算与无损文本格式。"""
from datetime import date, datetime, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import re

MAX_ARITHMETIC_DIGITS = 4096
ARITHMETIC = {"decimal_values": "string", "division_rounding": "ROUND_HALF_EVEN", "minimum_precision": 34, "precision": "adaptive_input_span", "maximum_digits": MAX_ARITHMETIC_DIGITS}


def missing(value):
    return value is None or isinstance(value, str) and not value.strip()


def numeric(value):
    """复用原numeric：布尔、容器、空串和非有限数均非数值，不能当成零。"""
    if value is None or isinstance(value, (bool, dict, list)) or str(value).strip() == "":
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def arithmetic_precision(numbers, *, products=False):
    """复用原位宽预算，限制极端指数；乘法与除法留足进位和循环小数精度。"""
    nonzero = [value for value in numbers if value]
    if not nonzero:
        return 34
    highest = max(value.adjusted() for value in nonzero)
    lowest = min(value.as_tuple().exponent for value in nonzero)
    width = highest - lowest + 1
    precision = max(34, width * (2 if products else 1) + 2 * len(str(len(numbers))) + 16)
    if precision > MAX_ARITHMETIC_DIGITS or max(abs(highest), abs(lowest)) > MAX_ARITHMETIC_DIGITS:
        raise ValueError("数值位数超出计算范围")
    return precision


def decimal_text(value):
    """复用原格式化，不经normalize舍入，也不将大指数展开为巨型字符串。"""
    if not value:
        return "0"
    if max(abs(value.adjusted()), abs(value.as_tuple().exponent)) > MAX_ARITHMETIC_DIGITS:
        return str(value)
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def decimal_value(value):
    number = numeric(value)
    if number is None:
        raise ValueError("值不是有限数值")
    arithmetic_precision([number])
    return number


def typed(value, data_type):
    """显式转换标量；日期不猜格式，时间必须带时区，失败交由调用方定位原始行。"""
    if missing(value):
        return None
    if isinstance(value, (dict, list)):
        raise ValueError("值不是标量")
    if data_type == "text":
        return str(value)
    if data_type in ("decimal", "integer"):
        result = decimal_value(value)
        if data_type == "integer" and result != result.to_integral_value():
            raise ValueError("值不是整数")
        return result
    if data_type == "boolean":
        if isinstance(value, bool):
            return value
        normalized = str(value).casefold().strip()
        if normalized in ("true", "1"):
            return True
        if normalized in ("false", "0"):
            return False
        raise ValueError("值不是布尔值")
    if data_type == "date":
        text = str(value)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError("日期应为YYYY-MM-DD")
        return date.fromisoformat(text)
    if data_type == "datetime":
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("时间缺少时区")
        return parsed.astimezone(timezone.utc)
    raise ValueError("不支持此类型")


def json_scalar(value):
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def calculate(op, numbers, *, digits=None):
    """封闭算术运算，按实际位宽建立局部Decimal上下文，不改变进程全局精度。"""
    precision = arithmetic_precision(numbers, products=op in ("multiply", "divide"))
    if op == "round":
        precision = max(precision, numbers[0].adjusted() + 1 + digits + 2)
        if precision > MAX_ARITHMETIC_DIGITS:
            raise ValueError("数值位数超出计算范围")
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
        if op == "add":
            result = numbers[0] + numbers[1]
        elif op == "subtract":
            result = numbers[0] - numbers[1]
        elif op == "multiply":
            result = numbers[0] * numbers[1]
        elif op == "divide":
            if numbers[1] == 0:
                raise ValueError("除数不能为零")
            result = numbers[0] / numbers[1]
        elif op == "round":
            result = numbers[0].quantize(Decimal(1).scaleb(-digits))
        elif op == "sum":
            result = sum(numbers, Decimal(0))
        elif op == "avg":
            result = sum(numbers, Decimal(0)) / len(numbers)
        else:
            raise ValueError("不支持此计算")
        arithmetic_precision([result])
        return result
