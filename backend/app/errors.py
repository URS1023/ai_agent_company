# -*- coding: utf-8 -*-
"""Unified exception hierarchy and FastAPI error responses.

职责：定义全后端统一的业务异常基类与细分异常，并约定统一响应体的错误码。

系统位置：业务模块、服务层、工具/模型调用方都抛出这里的异常，由 main.py 注册的异常
处理器转换为标准 JSON 错误响应（含 HTTP 状态码与 code 字段），前端按 code 分支处理。

约束：异常子类的基础字段是 (status_code, code)，两者必须成对设计——status_code 面向前端
与网关的重试/提示策略（4xx 是调用方问题，5xx 是依赖或环境问题），code 是前端可稳定判断的
契约字符串，不随文案变化；新增子类时不要在业务代码里另起错误码常量。
"""


class AppError(Exception):
    """所有业务异常的基类（默认 400 / app_error），携带 message 与额外上下文。

    extra 用于把 detail 等结构化信息透出到响应体，注意不要放入凭据或内部堆栈。
    """

    status_code = 400
    code = "app_error"

    def __init__(self, message: str, **extra):
        """参数：message 为面向调用方的中文说明；**extra 为附加到响应体的键值。"""
        super().__init__(message)
        self.message = message
        self.extra = extra


class ValidationError(AppError):
    """请求参数或业务校验失败（422），提示可展示给用户。"""

    status_code = 422
    code = "validation_error"


class SchemaValidationError(ValidationError):
    """模型/结构化产物未通过 schema 校验（422）。

    与普通校验失败区分开，调用方据此判断是否值得触发一次模型重试或 JSON 修复。
    """

    code = "schema_validation_error"


class AuthError(AppError):
    """未认证：缺少、过期或不可解析的会话（401），前端应跳转登录。"""

    status_code = 401
    code = "unauthorized"


class PermissionErrorEx(AppError):
    """已认证但无权访问该动作/资源（403）。

    命名带 Ex 是为避开 Python 内建 PermissionError。
    """

    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    """资源不存在或对当前数据范围不可见（404）。

    注意：越权访问统一按 404 返回，避免通过状态码探测资源是否存在。
    """

    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    """与现有状态冲突（409），如唯一键重复、并发写同一资源。"""

    status_code = 409
    code = "conflict"


class ToolExecutionError(AppError):
    """工具执行失败（502），属于下游依赖问题，可提示重试。"""

    status_code = 502
    code = "tool_execution_error"


class SandboxError(AppError):
    """沙箱执行失败（502），如超时、内存超限或进程被终止。"""

    status_code = 502
    code = "sandbox_error"


class ModelError(AppError):
    """模型调用失败（502）。"""

    status_code = 502
    code = "model_error"


class ModelResponseIncompleteError(ModelError):
    """The provider did not deliver an executable tool call or complete answer.

    Callers may regenerate the model response once, without replaying tools.
    The reason is protocol metadata only; partial output is never trusted data.
    """

    code = "model_response_incomplete"


class ModelBalanceError(AppError):
    """Account action is required; layout/JSON repair retries cannot fix it.

    账户欠费/额度耗尽：必须由人工充值或换密钥，重试与修复流程都应立即放弃。
    """

    status_code = 502
    code = "model_balance_exhausted"


class ModelRateLimitError(AppError):
    """模型限流（503）：可退避重试，不应计入业务失败。"""

    status_code = 503
    code = "model_rate_limited"


class ModelUnavailableError(AppError):
    """模型服务不可用（503）：供应商侧故障或网络异常，可稍后重试。"""

    status_code = 503
    code = "model_unavailable"
