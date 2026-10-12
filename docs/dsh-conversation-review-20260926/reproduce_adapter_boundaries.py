"""离线执行原函数 AST，核验配置、失败恢复与交付判定；不导入应用或连接数据库。"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("adapter-boundary-evidence.json")


def load_nodes(relative, names, env):
    """仅编译指定定义，保留真实业务函数体，隔离应用初始化副作用。"""
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))
    selected = [node for node in tree.body if getattr(node, "name", None) in names or
                isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / relative), "exec"), env)
    return tree


env = {"settings": SimpleNamespace(HARNESS_CONTEXT_WINDOW=32768, HARNESS_MAX_OUTPUT_TOKENS=4096), "urlsplit": urlsplit}
load_nodes("backend/app/modules/harness/context.py", {"model_configuration"}, env)
configuration = env["model_configuration"]
models = []
for protocol, options in [
    ("deepseek-messages", {"thinking": "disabled"}),
    ("deepseek-messages", {"thinking": "disabled", "reasoning_effort": "off"}),
    ("openai-completions", {"thinking": "disabled"}),
    ("openai-completions", {"thinking": "disabled", "reasoning_effort": "high"}),
]:
    supplied = {"protocol": protocol, **options}
    actual = configuration({"model": "fixture", "base_url": "https://fixture.invalid", "capabilities": {"runtime_options": supplied}})
    actual.pop("apiKey")
    models.append({"input": supplied, "python_to_node": actual})
assert models[0]["python_to_node"]["thinking"] == "enabled"
assert models[1]["python_to_node"]["thinking"] == "disabled"


class Expr:
    """最小谓词对象，只在内存记录上求值。"""
    def __init__(self, fn):
        self.fn = fn


class Field:
    """模拟本段查询实际使用的字段比较。"""
    def __init__(self, side, name):
        self.side, self.name = side, name

    def __eq__(self, value):
        return Expr(lambda row: getattr(row[self.side], self.name) == value)

    def __ne__(self, value):
        return Expr(lambda row: getattr(row[self.side], self.name) != value)

    def in_(self, values):
        return Expr(lambda row: getattr(row[self.side], self.name) in values)


class Query:
    """忽略无副作用连接声明，完整应用 where 谓词。"""
    def join(self, *args):
        return self

    def where(self, *predicates):
        self.predicates = predicates
        return self


class FakeDB:
    """仅提供合成历史运行；无数据库驱动。"""
    def __init__(self, rows):
        self.rows = rows

    def execute(self, query):
        rows = [row for row in self.rows if all(predicate.fn(row) for predicate in query.predicates)]
        return SimpleNamespace(all=lambda: rows)


class Failure(Exception):
    """记录真实函数抛出的稳定错误码。"""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


run_fields = {key: Field(1, key) for key in ("id", "workspace_id", "started_by_user_id", "conversation_id")}
call_fields = {key: Field(0, key) for key in ("run_id", "status", "error_code")}
env = {"ToolCall": SimpleNamespace(**call_fields), "Run": SimpleNamespace(**run_fields),
       "HarnessFailure": Failure, "select": lambda *args: Query(),
       "or_": lambda *args: Expr(lambda row: any(arg.fn(row) for arg in args))}
runtime_tree = load_nodes("backend/app/modules/harness/runtime.py", {"_MODEL_FAULT_MARKERS", "_database_failure", "_require_reconciled_tools"}, env)
tool_cases = []
for code, summary in [("tool_internal_error", "图表颜色解析失败"), ("tool_error", "文件格式不支持"),
                      ("validation_error", "参数需要修正"), ("model_unavailable", "模型服务暂时不可用")]:
    tool_cases.append({"error_code": code, "summary": summary,
                       "treated_as_database_failure": env["_database_failure"](SimpleNamespace(status="failed", error_code=code, error_summary=summary))})
assert tool_cases[0]["treated_as_database_failure"]
previous = SimpleNamespace(id="old-run", workspace_id="w", started_by_user_id="u", conversation_id="c", context_snapshot={"chat_engine": "harness"})
failed_call = SimpleNamespace(run_id="old-run", status="failed", error_code="tool_internal_error")
db = FakeDB([(failed_call, previous)])
continuations = []
for next_id in ("next-run", "next-next-run"):
    current = SimpleNamespace(id=next_id, workspace_id="w", started_by_user_id="u", conversation_id="c")
    try:
        env["_require_reconciled_tools"](db, current)
    except Failure as failure:
        continuations.append({"run": next_id, "error_code": failure.code, "message": str(failure)})
assert len(continuations) == 2


audit_env = {"re": re}
load_nodes("backend/app/modules/runtime/grounding_check.py", {"check_grounding", "extract_numbers", "_evidence_numbers", "_NUMBER", "_UNITS"}, audit_env)
delivery = audit_env["check_grounding"](committed_text="文档已制作完成，请下载附件。", evidence=[], artifacts=[])
assert delivery["status"] == "pass" and not delivery["warnings"]


event_env = {}
load_nodes("backend/app/modules/harness/projection.py", {"native_usage"}, event_env)
run_node = next(node for node in runtime_tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_chat")
settlement_branch = next(node for node in ast.walk(run_node) if isinstance(node, ast.If) and ast.unparse(node.test).startswith("native and native.get('type') in"))
history = []
pending = [SimpleNamespace(metadata_json={"purpose": "conversation"})]


def settle_model(row, status, **fields):
    """复用真实结算分支，仅替代数据库写入。"""
    history.append({"status": status, **fields})
    pending.remove(row)


event_env.update({"pending_models": pending, "settle_model": settle_model, "fixtures": [{
    "type": "assistant/attempt", "seq": 1, "data": {"stream": [
        {"chunk": {"type": "usage", "usage": {"inputTokens": 1, "outputTokens": 0}}},
        {"chunk": {"type": "finish", "reason": {"kind": "error", "failure": {"code": "SERVER", "message": "retry"}}}},
    ]},
}]})
body = ast.Module(body=[ast.For(target=ast.Name(id="native", ctx=ast.Store()), iter=ast.Name(id="fixtures", ctx=ast.Load()), body=[settlement_branch], orelse=[])], type_ignores=[])
exec(compile(ast.fix_missing_locations(body), "real-runtime-settlement-branch", "exec"), event_env)
assert history[0]["status"] == "completed"

evidence = {"execution": "AST 原函数 + 内存伪对象；0 数据库、0 网络、0 服务启动",
            "model_configuration": models, "generic_tool_failure": tool_cases,
            "same_conversation_after_definite_tool_failure": continuations,
            "artifact_claim_with_no_artifacts": delivery, "native_error_attempt_accounted_as": history}
OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False, indent=2))
