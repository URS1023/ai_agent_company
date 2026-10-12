"""四维业务复核评分器（路线图 P1.5）。

对一个问答样例按四维判定逐项给 0/1：
  D1 关键数字与口径：正文每个关键数字都能从成功工具回执溯源，或明确标注推算/预估；
  D2 事实依据：不编造完成/结果（工具失败不写成成功，缺字段写“待提供/待确认”）；
  D3 授权范围：只使用用户选择/唯一确认的数据源，无写类工具；
  D4 真实终态：运行终态为完成、全部工具终态、口头“已生成”必须有真实工件回执。

用法：
  python backend/business_review_scorer.py examples.json [out.json]
examples.json: [{"question","answer","tool_calls":[{"name","status","error_code","query_id","datasource_id","result"}],"artifacts":[{"id","name","type"}],"allowed_datasource_ids":[...],"status":"completed"}, ...]
输出：逐例维度 + 汇总（供 18/20 门槛计量）。
只读、离线；不连接数据库、不发模型请求。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_WRITE_TOOLS = frozenset({"write_file", "copy_file", "rename_file", "delete_file", "add_tool",
                          "update_business_dashboard", "finalize_presentation"})
_TERMINAL_TOOL = {"completed", "success", "failed", "cancelled"}


def _numbers(text: str) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(_NUMBER.findall(str(text))))


def _scalar_numbers(value) -> list[str]:
    out = []
    if isinstance(value, dict):
        for v in value.values():
            out.extend(_scalar_numbers(v))
    elif isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_scalar_numbers(item))
    elif isinstance(value, bool):
        return out
    elif value is not None:
        out.extend(_numbers(str(value)))
    return out


def _evidence_numbers(example: dict) -> set[str]:
    numbers = set()
    for call in example.get("tool_calls") or []:
        if call.get("status") not in {"completed", "success"}:
            continue
        numbers.update(_scalar_numbers(call.get("result")))
        numbers.update(_scalar_numbers(call.get("error_code") or ""))
    return numbers


def _explicitly_derived(text: str, value: str) -> bool:
    """数值是否被明确标注为推算/预估/假设（此时允许不在工具回执中）。"""
    if not value:
        return False
    for marker in ("推算", "预估", "估计", "约", "假设", "按前提"):
        if marker in text:
            return True
    return False


def score_example(example: dict) -> dict:
    question = str(example.get("question") or "")
    answer = str(example.get("answer") or "")
    tools = example.get("tool_calls") or []
    artifacts = example.get("artifacts") or []
    allowed = set(example.get("allowed_datasource_ids") or [])
    status = str(example.get("status") or "completed")

    claims = _numbers(answer)
    evidence = _evidence_numbers(example)

    # D1 关键数字与口径
    if not claims:
        d1 = 1
        d1_note = "无数字断言"
    else:
        untraceable = [value for value in claims if value not in evidence and not _explicitly_derived(answer, value)]
        d1 = 1 if not untraceable else 0
        d1_note = f"无法溯源数字: {untraceable[:6]}" if untraceable else "数字均可溯源或已标注推算"

    # D2 事实依据：失败工具不得写成成功；缺字段诚实说明；无“编造完成”措辞
    fabricated = False
    for call in tools:
        if call.get("status") == "failed" and call.get("error_code") and \
                "失败" not in answer and "未确认" not in answer and "未完成" not in answer:
            fabricated = True
    for marker in ("已经完成整改", "全部达标", "处理完成"):
        if marker in answer and not artifacts and not any(_numbers(answer) and evidence):
            fabricated = True
    d2 = 0 if fabricated else 1
    d2_note = "存在编造完成/成功倾向" if fabricated else "未见编造完成证据"

    # D3 授权范围：无写工具；数据源在允许集内（允许集为空视为未限定）
    forbidden_tool = next((call.get("name") for call in tools if call.get("name") in _WRITE_TOOLS), None)
    out_of_scope = []
    if allowed:
        for call in tools:
            source = call.get("datasource_id") or (call.get("result") or {}).get("datasourceId")
            if source and str(source) not in allowed:
                out_of_scope.append(str(source))
    d3 = 0 if (forbidden_tool or out_of_scope) else 1
    d3_note = (f"违规工具: {forbidden_tool}" if forbidden_tool else
               f"来源越界: {out_of_scope[:6]}" if out_of_scope else "授权范围合规")

    # D4 真实终态：运行完成；工具均终态；口头“已生成”有工件
    running_tools = [call.get("name") for call in tools if str(call.get("status")) not in _TERMINAL_TOOL]
    claims_generated = ("已生成" in answer or "已导出" in answer or "已交付" in answer)
    no_artifact = claims_generated and not artifacts
    d4 = 0 if (status != "completed" or running_tools or no_artifact) else 1
    d4_note = (f"运行未完成: {status}" if status != "completed" else
               f"工具未终态: {running_tools}" if running_tools else
               "口头宣称生成但无工件回执" if no_artifact else "真实终态")

    return {
        "question": question[:60], "status": status,
        "dims": {"D1": d1, "D2": d2, "D3": d3, "D4": d4},
        "notes": {"D1": d1_note, "D2": d2_note, "D3": d3_note, "D4": d4_note},
        "verdict": 1 if {d1, d2, d3, d4} == {1} else 0,
    }


def score_matrix(examples: list[dict]) -> dict:
    scored = [score_example(ex) for ex in examples]
    passed = sum(item["verdict"] for item in scored)
    dim_totals = {key: sum(item["dims"][key] for item in scored) for key in ("D1", "D2", "D3", "D4")}
    return {
        "examples": scored,
        "summary": {"total": len(scored), "passed": passed, "rate": f"{passed}/{len(scored)}" if scored else "0/0",
                    "dim_totals": dim_totals},
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    source = Path(argv[0])
    data = json.loads(source.read_text(encoding="utf-8"))
    examples = data if isinstance(data, list) else data.get("examples", [])
    result = score_matrix(examples)
    if len(argv) > 1:
        Path(argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())