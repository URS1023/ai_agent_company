"""用伪会话离线复现调度把失败或等待中的业务执行记为完成；无数据库和网络连接。"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace


class FakeExpression:
    """接受 SQL 构造操作但不执行查询。"""

    def __eq__(self, other):
        return self

    def where(self, *conditions):
        return self


class FakeRun(SimpleNamespace):
    """只保留调度函数会写入的字段。"""

    workspace_id = FakeExpression()
    idempotency_key = FakeExpression()

    def __init__(self, **values):
        super().__init__(id="fake-run", output={}, error_summary="", **values)


def main() -> None:
    """执行当前源码的 run_job，替换所有查询、提交和实际业务分发。"""
    sys.stdout.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    source = root / "backend/app/modules/system_tools/schedules.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name in {"run_job", "_bounded_output"}]
    selected.insert(0, ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0))
    ast.fix_missing_locations(tree := ast.Module(body=selected, type_ignores=[]))
    evidence = []
    for business_status in ("failed", "waiting", "cancelled", "completed"):
        row = SimpleNamespace(
            id="fake-job", status="active", task_type="workflow", version=1,
            schedule_kind="interval", schedule_expression="60", timezone="UTC",
        )
        ctx = SimpleNamespace(workspace_id="fixture", user_id="fixture-user", db=SimpleNamespace(
            scalar=lambda statement: None, add=lambda value: None, commit=lambda: None,
        ))
        scope = {
            "datetime": datetime, "json": json, "ScheduledJobRun": FakeRun,
            "require_permission": lambda *args: None,
            "scoped_record": lambda *args: row,
            "ScheduledJob": object, "check_version": lambda *args: None,
            "select": lambda *args: FakeExpression(), "flush_changes": lambda *args: None,
            "_dispatch": lambda *args: {"status": business_status, "errorMessage": "fixture failure" if business_status == "failed" else ""},
            "next_run": lambda *args: datetime.utcnow() + timedelta(seconds=60),
            "run_view": lambda run: {"status": run.status, "output": run.output, "errorSummary": run.error_summary},
        }
        exec(compile(tree, str(source), "exec"), scope)
        result = scope["run_job"](ctx, row.id, row.version, "fixture-key")
        evidence.append({"businessStatus": business_status, "scheduleResult": result})
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
