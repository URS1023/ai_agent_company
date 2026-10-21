"""导入依赖必须在连接前完整就绪；异常诊断不得泄露业务参数。"""
from dataclasses import replace
import json
import sys

from tools.demo_data.import_mysql import failure_diagnostics
from tools.demo_data.package import Plan
from test_demo_data_import import MemoryDatabase, csv_asset, runner


def test_driver_diagnostics_keep_numeric_code_without_secret_message():
    try:
        raise RuntimeError(2013, "SELECT private_password FROM confidential_table", {"token": "private-token"})
    except RuntimeError as error:
        result = failure_diagnostics(error)
    text = json.dumps(result)
    assert result["exception_type"] == "builtins.RuntimeError"
    assert result["numeric_code"] == 2013
    assert result["frames"][-1]["file"] == "test_demo_import_diagnostics.py"
    assert all(value not in text for value in ("SELECT", "private_password", "confidential_table", "private-token"))


def test_missing_parquet_dependency_fails_before_database_connection(tmp_path, monkeypatch):
    original, asset = csv_asset(tmp_path, 1)
    plan = Plan(original.package_root, (replace(asset, format="parquet"),))
    database = MemoryDatabase()
    monkeypatch.setitem(sys.modules, "pytz", None)
    result = runner(plan, tmp_path, database, "dependency-failure.json").run()
    assert result["status"] == "failed"
    assert result["error"]["code"] == "runtime_dependency_invalid"
    assert result["connections"] == 0 and result["statements"] == 0
    assert not database.connections and not result["requires_query_stop_check"]


def test_connection_failure_receipt_is_diagnostic_but_redacted(tmp_path):
    plan, _ = csv_asset(tmp_path, 1)
    database = MemoryDatabase()
    importer = runner(plan, tmp_path, database, "connection-failure.json")

    def unavailable(**kwargs):
        raise RuntimeError(2003, "password=private-value; SELECT sensitive_column")

    importer.connection_factory = unavailable
    result = importer.run()
    assert result["status"] == "failed" and result["connections"] == 1
    assert result["diagnostics"]["numeric_code"] == 2003
    assert result["diagnostics"]["exception_type"] == "builtins.RuntimeError"
    assert result["rows_committed_this_run"] == 0
    text = (tmp_path / "connection-failure.json").read_text(encoding="utf-8")
    assert "private-value" not in text and "sensitive_column" not in text
