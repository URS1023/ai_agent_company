"""Transport failures must not become successful tool results.

中文说明：传输层失败（超时/断连/5xx）绝不能变成"工具执行成功"的结果。
隔离方式：SimpleNamespace 桩 + 子进程/临时目录夹具，不连业务库。
边界：失败必须以失败状态向上呈现，不允许空结果伪装成功。
"""
from types import SimpleNamespace

import pytest

from app.config import settings
from app.errors import SandboxError, ValidationError
from app.modules.analytics.sql_safety import parse_read_query
from app.providers.tool import sandbox_provider
from app.providers.tool.base import ToolSpec


@pytest.mark.parametrize("returncode,stdout", [
    (1, ""), (-1, '{"status":"success"}'), (0, ""),
    (0, "not json"), (0, "[]"), (0, "{}"), (0, '{"status":"running"}'),
])
def test_sandbox_invalid_process_results_are_failures(tmp_path, monkeypatch, returncode, stdout):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(sandbox_provider.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=returncode, stdout=stdout, stderr="private diagnostic"))
    with pytest.raises(SandboxError) as error:
        sandbox_provider.invoke(ToolSpec("python_execute", "test", "sandbox"),
                                {"code": "result = 1"}, {"run_id": "protocol"})
    assert "private diagnostic" not in str(error.value)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), "invalid"])
def test_sandbox_invalid_timeout_never_starts_worker(tmp_path, monkeypatch, timeout):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    def never_start(*args, **kwargs):
        pytest.fail("Invalid timeout started a subprocess")
    monkeypatch.setattr(sandbox_provider.subprocess, "run", never_start)
    with pytest.raises(SandboxError):
        sandbox_provider.invoke(ToolSpec("python_execute", "test", "sandbox"),
                                {"code": "result = 1", "timeout_seconds": timeout}, {"run_id": "timeout"})


@pytest.mark.parametrize("sql", ["SELECT SLEEP(10)", "SELECT LOAD_FILE('/tmp/offline')",
    "SELECT 1 INTO OUTFILE '/tmp/offline'", "SELECT 1; DELETE FROM sales", "PRAGMA database_list",
    "SELECT 'unterminated", "SELECT /* unterminated"])
def test_query_preflight_rejects_unsafe_sql_without_metadata(sql):
    with pytest.raises(ValidationError):
        parse_read_query(sql, "mysql")
