"""Build a source-only route baseline and an incremental historical issue ledger.

No application imports, services, database connections or network requests.
Existing issue dispositions and evidence survive regeneration.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/audit/acceptance_20260920"
HISTORICAL = ROOT / "docs/audit/review_20260917/reports"
IDENTIFIER = r"\b([A-Z][A-Z0-9-]*-\d{3})\b"


def historical_issues() -> list[dict]:
    """从 docs/audit/review_20260917/reports 的历史报告里抽取问题台账条目。

    只读 b/c/f 前缀的分域报告；表格行与标题行分别用两种正则识别编号，严重度优先取同行 P0–P3，
    否则回退到所在段落的「级别」行。返回条目只带原报告位置与原文，状态一律为「待当前代码复核」。
    数量必须恰好 320，否则抛 RuntimeError（历史数量变化必须先人工确认，不允许静默少算）。
    """
    records = []
    for report in sorted(HISTORICAL.glob("*.md")):
        if not re.match(r"^[bcf]\d\d_", report.name):
            continue
        lines = report.read_text(encoding="utf-8-sig").splitlines()
        found = {}
        for index, line in enumerate(lines):
            if line.startswith("#") or re.match(r"^\|\s*ID\s*\|", line):
                match = re.search(IDENTIFIER, line)
            else:
                match = re.match(r"^\|\s*(?:\*\*|`)?([A-Z][A-Z0-9-]*-\d{3})(?:\*\*|`)?\s*\|\s*(?:\*\*)?P[0-3]", line)
            if match:
                found.setdefault(match.group(1), index)
        for identifier, index in found.items():
            excerpt = "\n".join(lines[index:index + 18])
            inline = re.search(r"\bP[0-3]\b", lines[index])
            severity = inline or re.search(r"\|\s*级别\s*\|\s*(P[0-3])", excerpt)
            records.append({
                "key": report.stem + ":" + identifier, "id": identifier,
                "severity_in_domain_report": severity.group(0) if inline else severity.group(1) if severity else "待核对",
                "source": report.relative_to(ROOT).as_posix(), "line": index + 1,
                "original_definition": lines[index], "status": "待当前代码复核",
                "current_locations": [], "evidence": [], "notes": "",
            })
    if len(records) != 320:
        raise RuntimeError(f"Historical count changed: {len(records)}; inspect before claiming coverage")
    return records


def endpoint_inventory() -> tuple[list[dict], dict]:
    """用 AST 静态解析后端所有路由声明，返回 (路由行, 源文件 sha256 表)。

    只识别 APIRouter/FastAPI 变量上的方法装饰器，收集前缀、依赖、响应模型与分页参数名；
    不导入应用、不连数据库，因此「鉴权是否真正生效」仍需沿依赖与业务代码人工复核。
    """
    rows, hashes = [], {}
    for path in sorted((ROOT / "backend/app").rglob("*.py")):
        data = path.read_bytes()
        relative = path.relative_to(ROOT).as_posix()
        hashes[relative] = hashlib.sha256(data).hexdigest()
        tree = ast.parse(data.decode("utf-8-sig"))
        routers = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if ast.unparse(node.value.func).split(".")[-1] not in {"APIRouter", "FastAPI"}:
                continue
            keywords = {kw.arg: kw.value for kw in node.value.keywords}
            prefix = keywords.get("prefix", ast.Constant(""))
            prefix_value = prefix.value if isinstance(prefix, ast.Constant) else "<dynamic-prefix>"
            for target in node.targets:
                if isinstance(target, ast.Name):
                    routers[target.id] = (prefix_value, ast.unparse(keywords["dependencies"]) if "dependencies" in keywords else "")
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            signature = ast.unparse(function.args)
            for decorator in function.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                owner, method = ast.unparse(decorator.func.value), decorator.func.attr
                if owner not in routers or method not in {"get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket"}:
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                prefix, dependencies = routers[owner]
                options = {kw.arg: ast.unparse(kw.value) for kw in decorator.keywords}
                rows.append({
                    "file": relative, "line": decorator.lineno, "function": function.name,
                    "method": method.upper(), "path": prefix + decorator.args[0].value,
                    "signature": signature, "router_dependencies": dependencies,
                    "decorator_dependencies": options.get("dependencies", ""),
                    "response_model": options.get("response_model", ""),
                    "paging_parameters": [arg.arg for arg in [*function.args.args, *function.args.kwonlyargs]
                                          if arg.arg in {"limit", "offset", "page", "page_size", "pageSize"}],
                    "verification": "静态声明；权限有效性需沿依赖和业务代码复核",
                })
    return rows, hashes


def frontend_inventory() -> list[dict]:
    """Enumerate explicit Vue route declarations and legacy HTML entry files.

    This is a declaration baseline, not proof that navigation reaches a page.
    Relative Vue child paths remain literal rather than guessed at runtime.
    """
    rows = []
    router = ROOT / "apps/analytics/src/router.ts"
    for index, line in enumerate(router.read_text(encoding="utf-8-sig").splitlines(), 1):
        match = re.match(r"\s*\{ path: ['\"]([^'\"]*)['\"]", line)
        if match:
            component = re.search(r"import\(['\"]([^'\"]+)['\"]\)", line)
            permission = re.search(r"permission: ['\"]([^'\"]+)['\"]", line)
            rows.append({"kind": "Vue route", "path": match[1],
                         "file": router.relative_to(ROOT).as_posix(), "line": index,
                         "component": component[1] if component else "",
                         "redirect": "redirect:" in line,
                         "permission": permission[1] if permission else "",
                         "declaration": line.strip()})
    for path in sorted((ROOT / "apps/web").glob("*.html")):
        rows.append({"kind": "HTML entry", "path": path.name,
                     "file": path.relative_to(ROOT).as_posix(), "line": 1,
                     "component": "", "redirect": False, "permission": "",
                     "declaration": "实际 URL、鉴权与嵌入行为需结合后端页面路由复核"})
    return rows


def main() -> None:
    """重新生成验收基线产物到 docs/audit/acceptance_20260920 下（台账、接口清单、前端页面清单）。

    关键约束：已有台账中的人工结论（status/current_locations/evidence/notes）按 key 保留，
    重新生成只补新增条目，避免覆盖复核结果。全过程只读源码与历史报告，不导入应用、不连库、不发请求。
    """
    ledger_path = OUT / "issue_ledger.json"
    existing = {row["key"]: row for row in json.loads(ledger_path.read_text(encoding="utf-8"))} if ledger_path.exists() else {}
    issues = historical_issues()
    for issue in issues:
        previous = existing.get(issue["key"], {})
        for key in ("status", "current_locations", "evidence", "notes"):
            if key in previous:
                issue[key] = previous[key]
    ledger_path.write_text(json.dumps(issues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    routes, hashes = endpoint_inventory()
    (OUT / "endpoints.json").write_text(json.dumps(routes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    baseline = {"generated_at": datetime.now(timezone.utc).isoformat(), "source_sha256": hashes,
                "endpoint_count": len(routes), "historical_issue_count": len(issues)}
    (OUT / "baseline.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 当前验收问题台账", "", "历史 14 个分域共 320 条，逐条保留原编号；同根因仍保留各自入口。",
             "原报告严重度与总报告升级后的严重度可能不同；当前优先级需结合复验确定。",
             "未复核不等于仍有缺陷，代码修复不等于全链路验收通过。", "",
             "| 原编号 | 原分域级别 | 原报告 | 当前状态 | 证据 |", "|---|---|---|---|---|"]
    for issue in issues:
        source = "../review_20260917/reports/" + Path(issue["source"]).name
        evidence = "; ".join(issue["evidence"]).replace("|", "\\|")
        lines.append(f"| {issue['id']} | {issue['severity_in_domain_report']} | [{Path(issue['source']).stem}:{issue['line']}]({source}) | {issue['status']} | {evidence} |")
    (OUT / "ISSUE_LEDGER.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    route_lines = ["# 当前接口与后端页面声明清单", "", f"共 {len(routes)} 条；AST 解析装饰器、函数签名和路由级依赖。",
                   "鉴权存在与否不能仅凭 Depends 判断，本表保留原始签名供逐点复核。", "",
                   "| 方法 | 路径 | 位置 | 函数 | 分页参数 |", "|---|---|---|---|---|"]
    for row in routes:
        route_lines.append(f"| {row['method']} | `{row['path']}` | {row['file']}:{row['line']} | {row['function']} | {', '.join(row['paging_parameters'])} |")
    (OUT / "ENDPOINTS.md").write_text("\n".join(route_lines) + "\n", encoding="utf-8")
    pages = frontend_inventory()
    (OUT / "frontend_pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    page_lines = ["# 前端页面声明基线", "",
                  "包含 Vue 路由声明及旧前端 HTML 入口。相对子路径保留原文；重定向不是独立管理界面。",
                  "动态平台菜单、按钮操作与页面内部状态仍需逐点验收，本表不宣称全部可达。", "",
                  "| 类型 | 声明路径 | 位置 | 组件或重定向 | 显式路由权限 |", "|---|---|---|---|---|"]
    for page in pages:
        path = page['path'].replace('|', '\\|')
        target = "redirect" if page['redirect'] else page['component']
        page_lines.append(f"| {page['kind']} | `{path}` | {page['file']}:{page['line']} | {target} | {page['permission']} |")
    (OUT / "FRONTEND_PAGES.md").write_text("\n".join(page_lines) + "\n", encoding="utf-8")
    print(f"Historical issues: {len(issues)}; route declarations: {len(routes)}; source hashes: {len(hashes)}")
    print(f"Frontend declarations: {len(pages)}")


if __name__ == "__main__":
    main()
