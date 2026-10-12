"""模型恢复分类的隔离回归，不访问供应商、数据库或正在运行的服务。"""
from app.errors import ModelBalanceError, ModelRateLimitError, ModelUnavailableError
from app.modules.openwebui.runtime import _retryable_model_failure


def test_transient_model_failures_are_recoverable():
    """连接、限流和供应商不可用允许继续恢复。"""
    assert _retryable_model_failure(ModelUnavailableError("连接断开"))
    assert _retryable_model_failure(ModelRateLimitError("触发限流"))


def test_balance_failure_is_not_retried_forever():
    """余额不足属于配置/账户问题，不能用重复请求制造无意义空转。"""
    assert not _retryable_model_failure(ModelBalanceError("余额不足"))
