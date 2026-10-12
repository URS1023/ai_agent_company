"""Re-runnable offline checks for the AI/analytics backend fixes.

This script deliberately uses no application server, database session, model
provider, HTTP client, subprocess, or sandbox user code.  It exercises pure
parsers/normalizers and validates the durable idempotency schema contract.  A
separate child process should be used for the worker itself in deployment;
these checks only call the worker's boundary helpers with ordinary values.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _assert_permission(callable_, *args, **kwargs):
    try:
        callable_(*args, **kwargs)
    except PermissionError:
        return
    raise AssertionError("expected sandbox boundary PermissionError")


def main() -> None:
    from app.models import IdempotencyClaim
    from app.modules.capability.planner import extract_json
    from app.modules.presentation.engine import _infer_layout, _item_value
    from app.modules.reports.generation import _has_bound_data, _http_catalog_schema
    from app.providers.tool.compiler import reference_resolver
    from app.workers.sandbox_runner import _install_file_audit, _restricted_builtins

    assert extract_json('模型说明 {"reason":"保留 } 字符","object":"skill"}') == {
        "reason": "保留 } 字符", "object": "skill",
    }
    assert reference_resolver({"lookups": {"project": {"P01": "项目一"}}})("project", "项目一") == "P01"
    assert _item_value({"number": 0}) == "0"
    assert _infer_layout({"items": [
        {"title": "停机次数", "value": 0}, {"title": "告警次数", "value": 0},
    ]}) == "metrics"
    assert _has_bound_data({
        "query": {"sourceType": "http"},
        "config": {"dataStatus": "verified"},
    })

    columns = [
        SimpleNamespace(model_dump=lambda mode="json": {"columnName": "name", "dataType": "string", "comment": ""}),
        SimpleNamespace(model_dump=lambda mode="json": {"columnName": "api_token", "dataType": "string", "comment": "secret"}),
    ]
    schema = _http_catalog_schema(
        SimpleNamespace(id="http-1", name="设备接口", connection_config={}),
        SimpleNamespace(columns=columns, data=[{"name": "设备A", "api_token": "redacted"}],
                        queryId="q-1", datasourceVersion=1),
    )
    assert schema["sourceType"] == "http"
    assert schema["tables"][0]["columns"] == [{"columnName": "name", "dataType": "string", "comment": ""}]
    assert schema["tables"][0]["sampleRows"] == [{"name": "设备A"}]

    columns_in_unique = [column.name for column in next(
        constraint for constraint in IdempotencyClaim.__table__.constraints
        if getattr(constraint, "name", "") == "uq_idempotency_claim_scope_key"
    ).columns]
    assert columns_in_unique == ["workspace_id", "owner_user_id", "scope", "idempotency_key"]

    # Exercise the actual imported file APIs.  The audit hook is installed
    # only in this short-lived check process, and no user code is executed.
    with tempfile.TemporaryDirectory(prefix="test5-sandbox-check-") as temp:
        root = Path(temp).resolve()
        inside = root / "inside.txt"
        outside = root.parent / (root.name + "-outside.txt")
        inside.write_text("ok", encoding="utf-8")
        _install_file_audit(str(root))
        builtins = _restricted_builtins(str(root))
        assert builtins["open"](inside, encoding="utf-8").read() == "ok"
        assert io.open(inside, "r", encoding="utf-8").read() == "ok"
        fd = os.open(inside, os.O_RDONLY)
        try:
            assert os.read(fd, 2) == b"ok"
        finally:
            os.close(fd)
        assert Path(inside).read_text(encoding="utf-8") == "ok"
        _assert_permission(io.open, outside, "r", encoding="utf-8")
        _assert_permission(os.open, outside, os.O_RDONLY)
        _assert_permission(Path(outside).read_text, encoding="utf-8")

    print("OFFLINE_AI_BACKEND_CHECKS_OK")


if __name__ == "__main__":
    main()
