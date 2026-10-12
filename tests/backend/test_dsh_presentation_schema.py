"""验证当前 DSH 声明和调用保留真实 PPT 嵌套契约，不连接业务源或真实模型。"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from app.errors import SchemaValidationError
from app.providers.tool.builtin_provider import builtin_specs
from app.providers.tool.validator import validate_arguments


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def presentation_tool():
    """从生产注册目录导出同一声明，测试不另写一份 schema 当作真值。"""
    return next(spec for spec in builtin_specs() if spec.name == "write_presentation_slides").to_openai_function()


@pytest.fixture
def presentation_arguments():
    """使用明显的合成内容覆盖两种面板结构、图表别名和表格标量边界。"""
    return {
        "draft_id": "synthetic-schema-draft",
        "slides": [
            {"slide_number": 1, "physical_page_number": 2, "title": "合成卡片", "layout": "cards",
             "section": "合同验证", "key_message": "正文与备注独立保留", "notes": "合成备注",
             "items": [
                 {"type": "card", "title": "明确标题", "label": "兼容标题", "description": "正文解释",
                  "text": "兼容正文", "data": "兼容解释"},
                 {"type": "metric", "title": "零值指标", "description": "零不能被当作缺失", "value": 0},
                 {"type": "metric", "title": "文本指标", "description": "保留文本单位", "value": "2项"},
             ]},
            {"slide_number": 2, "title": "合成图表", "layout": "chart",
             "chart": {"chart_type": "column", "categories": ["合成甲", "合成乙", "合成丙"],
                       "series": [{"name": "合成数值", "values": [0, -2, 1.25]},
                                  {"label": "兼容序列", "data": [3, 0, 4.5]}],
                       "source": "本测试内存样例", "insight": "包含零、负数与小数"},
             "items": [{"type": "chart", "chart_type": "line", "categories": ["合成时点"],
                        "series": [{"name": "内嵌序列", "values": [0]}], "source": "内嵌合成样例"}]},
            {"slide_number": 3, "title": "合成表格", "layout": "table",
             "table": {"headers": ["名称", "数值", "标记", "未知"], "columns": ["名称", "数值", "标记", "未知"],
                       "rows": [["合成甲", 0, False, None], ["合成乙", 1.25, True, ""]],
                       "source": "本测试内存样例", "insight": "标量类型保持"},
             "items": [{"type": "table", "headers": ["合成列"], "rows": [[None], [0]]}]},
            {"slide_number": 4, "title": "合成对比", "layout": "comparison",
             "left_title": "数组面板", "right_title": "对象面板",
             "left": [{"title": "左侧标题", "description": "左侧解释"}, "左侧短句"],
             "right": {"title": "右侧面板", "items": [{"title": "右侧标题", "description": "右侧解释"}, "右侧短句"]}},
        ],
    }


def assert_complete_schema(actual, expected):
    """先核对独立的业务字段路径，再逐字比较生产声明与实际下发声明。"""
    slide = actual["properties"]["slides"]["items"]["properties"]
    assert actual["properties"]["slides"]["maxItems"] == 50
    assert slide["items"]["items"]["properties"]["description"]["type"] == "string"
    assert slide["chart"]["properties"]["categories"]["items"]["type"] == "string"
    series = slide["chart"]["properties"]["series"]["items"]["properties"]
    assert series["values"]["items"]["type"] == series["data"]["items"]["type"] == "number"
    assert slide["table"]["properties"]["rows"]["items"]["items"]["type"] == ["string", "number", "boolean", "null"]
    for side in ("left", "right"):
        panel = slide[side]
        assert panel["items"]["properties"]["description"]["type"] == "string"
        assert panel["properties"]["items"]["items"]["properties"]["description"]["type"] == "string"
    assert actual == expected


@pytest.mark.parametrize("protocol", ["openai-completions", "deepseek-messages"])
def test_current_dsh_preserves_presentation_schema_and_worker_arguments(protocol, presentation_tool, presentation_arguments):
    """真实宿主、声明组装、适配器、响应解析和工具运行时只访问一个本机模型桩。"""
    expected = presentation_tool["function"]["parameters"]
    assert_complete_schema(expected, expected)
    assert validate_arguments(deepcopy(presentation_arguments), expected) == presentation_arguments
    # 子进程不继承实际模型密钥、代理和用户配置；原生依赖只从本树安装目录加载。
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
    completed = subprocess.run(
        ["node", str(ROOT / "tests/dsh/presentation_schema_probe.mjs")],
        input=json.dumps({"protocol": protocol, "tool": presentation_tool, "arguments": presentation_arguments}, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT, env=env, timeout=25,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    observed = json.loads(completed.stdout)
    for stage in ("registered", "assembled", "wire"):
        assert_complete_schema(observed[stage]["parameters"], expected)
        assert observed[stage]["name"] == "write_presentation_slides"
        assert observed[stage]["description"] == presentation_tool["function"]["description"]
    assert observed["model_requests"] == observed["connections"] == observed["worker_calls"] == 1
    assert observed["events"] == ["tool.started", "tool.completed"]
    assert observed["invocations_remaining"] == 0
    assert observed["worker_arguments"] == presentation_arguments
    assert validate_arguments(observed["worker_arguments"], expected) == presentation_arguments
    # 从实际 HTTP 声明删除深层字段，必须触发同一完整性断言；不能靠入参回显通过。
    damaged = deepcopy(observed["wire"]["parameters"])
    del damaged["properties"]["slides"]["items"]["properties"]["chart"]["properties"]["categories"]
    with pytest.raises((AssertionError, KeyError)):
        assert_complete_schema(damaged, expected)
    print(f"DSH_SCHEMA_EVIDENCE {protocol}: model_requests=1 connections=1 worker_calls=1 schema_loss_detected=1 business_selects=0 real_models=0")


@pytest.mark.parametrize(("case", "path"), [
    ("chart-number", "slides[1].chart.series[0].values[1]"),
    ("table-cell", "slides[2].table.rows[0][1]"),
    ("panel-description", "slides[3].left[0].description"),
    ("missing-categories", "slides[1].chart"),
])
def test_real_presentation_validator_rejects_deep_invalid_arguments(case, path, presentation_tool, presentation_arguments):
    """真实网关校验器拒绝深层坏值；此处不冒充 Node 原生注册层自带输入校验。"""
    slides = presentation_arguments["slides"]
    if case == "chart-number":
        slides[1]["chart"]["series"][0]["values"][1] = {"number": 2}
    elif case == "table-cell":
        slides[2]["table"]["rows"][0][1] = {"text": "不能当作单元格标量"}
    elif case == "panel-description":
        slides[3]["left"][0]["description"] = ["不能当作说明文本"]
    else:
        del slides[1]["chart"]["categories"]
    with pytest.raises(SchemaValidationError) as caught:
        validate_arguments(json.dumps(presentation_arguments, ensure_ascii=False), presentation_tool["function"]["parameters"])
    assert path in str(caught.value)
