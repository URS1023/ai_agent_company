"""Extract prompt seed data via SQL AST; never execute the old initialization SQL."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "lynx-ai-master/lynx-ai-parent/lynx-ai-web/src/main/resources/sql/report_prompt_manufacturing_seed.sql"
TARGET = ROOT / "backend/app/modules/reports/resources/prompt_seeds.json"


def literal(expression):
    """从 sqlglot AST 节点取出字面量值（Alias 先解包）；字符串返回原文、整数字面量返回 int，非字面量返回 None。"""
    if isinstance(expression, exp.Alias):
        expression = expression.this
    if isinstance(expression, exp.Literal):
        return expression.this if expression.is_string else int(expression.this)
    return None


def main():
    """从旧 Lynx 初始化 SQL 文本中提取提示词种子，写入 backend/app/modules/reports/resources/prompt_seeds.json。

    只做 SQL 静态解析（sqlglot），任何情况下都不执行该 SQL、不连库；输出里带源文件 sha256 供溯源。
    分类数不是 9 或提示词数不是 36 时抛 RuntimeError（防止源 SQL 变更后静默产出残缺数据）。
    """
    content = SOURCE.read_text(encoding='utf-8')
    statements = sqlglot.parse(content, read="mysql")
    categories = {}
    prompts = {}
    for statement in statements:
        if isinstance(statement, exp.Update) and statement.this.name == "report_prompt_category":
            values = {item.this.name: literal(item.expression) for item in statement.expressions}
            categories[values["name"]] = values
        elif isinstance(statement, exp.Insert) and statement.this.this.name == "report_prompt_category":
            values = [literal(item) for item in statement.expression.expressions]
            categories[values[0]] = dict(zip(("name", "icon", "color", "sort_order"), values[:4]))
    for statement in statements:
        if not isinstance(statement, exp.Insert) or statement.this.this.name != "report_prompt_template":
            continue
        selection = statement.expression
        if len(selection.expressions) == 6 and isinstance(selection.expressions[1], exp.Literal):
            values = [literal(item) for item in selection.expressions[1:5]]
            category = next(
                literal(condition.expression) for condition in selection.args["where"].find_all(exp.EQ)
                if isinstance(condition.this, exp.Column) and condition.this.name == "name" and literal(condition.expression) in categories
            )
            prompts[(category, values[0])] = {"category": category, **dict(zip(("name", "content", "description", "tags"), values))}
        else:
            for nested in selection.find_all(exp.Select):
                values = [literal(item) for item in nested.expressions]
                if len(values) == 5 and all(isinstance(value, str) for value in values) and values[0] in categories:
                    prompts[(values[0], values[1])] = dict(zip(("category", "name", "content", "description", "tags"), values))
    if len(categories) != 9 or len(prompts) != 36:
        raise RuntimeError(f"Unexpected source seed coverage: {len(categories)} categories / {len(prompts)} prompts")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps({"source": str(SOURCE.relative_to(ROOT.parent)).replace("\\", "/"),
                                  "sha256": hashlib.sha256(content.encode()).hexdigest(),
                                  "categories": list(categories.values()), "prompts": list(prompts.values())},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"categories": len(categories), "prompts": len(prompts), "source_unchanged": True}))


if __name__ == "__main__":
    main()
