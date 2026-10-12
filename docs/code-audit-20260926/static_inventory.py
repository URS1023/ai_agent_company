"""静态盘点项目源码与路由，不导入应用、不连接数据库或网络。"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".html", ".css", ".ps1", ".bat", ".cmd", ".sql"}
AREAS = {"backend", "apps", "services", "scripts", "tests", "configurations", "examples", ".githooks"}


def classify(path: Path) -> str:
    """将构建产物、第三方前端文件和一方源码区分，避免虚报人工审查范围。"""
    name = path.as_posix()
    if name.startswith("apps/web/assets/analytics/") or "/dist/" in name:
        return "generated"
    if name.startswith("apps/analytics/public/jimu-screen-reference/"):
        return "third_party_reference"
    if "/vendor/" in name or "/vendors/" in name or name.endswith(".min.js"):
        return "third_party"
    return "first_party"


def main() -> None:
    """为全部纳入范围的文件生成摘要，并解析一方 Python 语法与接口声明。"""
    names = subprocess.check_output(
        ["rg", "--files", "--hidden", "-g", "!.git", "-g", "!node_modules", "-g", "!.venv*",
         "-g", "!storage", "-g", "!.implementation", "-g", "!.tmp", "-g", "!artifacts"],
        cwd=ROOT, text=True, encoding="utf-8",
    ).splitlines()
    inventory, routes, errors = [], [], []
    for name in sorted(names):
        path = Path(name)
        if path.suffix.lower() not in EXTENSIONS:
            continue
        if len(path.parts) > 1 and path.parts[0] not in AREAS:
            continue
        raw = (ROOT / path).read_bytes()
        source = raw.decode("utf-8-sig", errors="replace")
        category = classify(path)
        row = {"path": path.as_posix(), "category": category, "lines": len(source.splitlines()),
               "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
               "python_ast": "not_applicable", "functions": "", "classes": ""}
        if path.suffix == ".py" and category == "first_party":
            try:
                tree = ast.parse(source, filename=name)
                row["python_ast"] = "passed"
                row["functions"] = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree))
                row["classes"] = sum(isinstance(n, ast.ClassDef) for n in ast.walk(tree))
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    for decorator in node.decorator_list:
                        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                            continue
                        if decorator.func.attr not in {"get", "post", "put", "patch", "delete", "websocket", "head", "options", "api_route"}:
                            continue
                        route_path = decorator.args[0] if decorator.args else None
                        if isinstance(route_path, ast.Constant) and isinstance(route_path.value, str):
                            routes.append({"file": path.as_posix(), "line": node.lineno, "method": decorator.func.attr.upper(),
                                           "local_path": route_path.value, "function": node.name})
            except SyntaxError as exc:
                row["python_ast"] = "failed"
                errors.append({"file": path.as_posix(), "line": exc.lineno, "message": exc.msg})
        inventory.append(row)
    with (OUTPUT / "source-inventory.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(inventory[0]))
        writer.writeheader()
        writer.writerows(inventory)
    (OUTPUT / "route-declarations.json").write_text(json.dumps(routes, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"scope": "项目一方源文件、构建产物及被纳入的第三方前端文件；不包含依赖、运行数据、上游仓库、参考材料与审查报告自身",
               "files": len(inventory), "categories": dict(Counter(row["category"] for row in inventory)),
               "first_party_lines": sum(row["lines"] for row in inventory if row["category"] == "first_party"),
               "python_parsed": sum(row["python_ast"] == "passed" for row in inventory),
               "python_errors": errors, "route_declarations": len(routes),
               "route_note": "路由为 AST 声明清单，未展开 APIRouter/include_router 前缀；不代表运行时挂载数量。"}
    (OUTPUT / "inventory-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
