"""领域错误保留稳定代码，前端按字段或当前动作展示。"""

from typing import Any


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields or {}
