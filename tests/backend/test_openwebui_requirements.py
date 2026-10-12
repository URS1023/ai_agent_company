"""交付意图使用纯文本样例回归，直接加载模块避免启动应用或连接数据库。"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


_PATH = Path(__file__).resolve().parents[2] / "backend/app/modules/openwebui/requirements.py"
_SPEC = spec_from_file_location("isolated_openwebui_requirements", _PATH)
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
requested_outputs = _MODULE.requested_outputs


@pytest.mark.parametrize(("content", "expected"), [
    ("请给我15页PPT和Excel", {"pptx": 15, "xlsx": True}),
    ("帮我做十五页PPT", {"pptx": 15}),
    ("帮我做一百零五页的幻灯片", {"pptx": 105}),
    ("生成二十页演示文稿", {"pptx": 20}),
    ("生成ＰＰＴ，要１５页", {"pptx": 15}),
    ("生成十五页PPT，PPT使用宋体", {"pptx": 15}),
    ("生成 PPT 共十五页", {"pptx": 15}),
    ("做一份15页的ppt然后做一份数字大屏放到word里面", {"pptx": 15, "dashboard": True, "docx": True, "embedded_dashboard": True}),
    ("可以帮我做 Word 和 Excel 吗？", {"docx": True, "xlsx": True}),
    ("能帮我生成 Word 和 Excel 吗？", {"docx": True, "xlsx": True}),
    ("是否能为我导出PDF？", {"pdf": True}),
    ("给我一份Excel文件", {"xlsx": True}),
    ("需要图饼并导出为word和excel", {"chart": True, "chart_types": ["pie"], "docx": True, "xlsx": True}),
    ("请绘制饼图、柱状图和折线图", {"chart": True, "chart_types": ["pie", "bar", "line"]}),
    ("请生成图表", {"chart": True}),
    ("导出Word、Excel、PDF", {"docx": True, "xlsx": True, "pdf": True}),
    ("生成Word，Excel，十五页PPT", {"docx": True, "xlsx": True, "pptx": 15}),
    ("Please generate a 15-page PPT and Excel", {"pptx": 15, "xlsx": True}),
    ("Give me a pie chart and Word", {"chart": True, "chart_types": ["pie"], "docx": True}),
])
def test_clear_delivery_requests(content, expected):
    assert requested_outputs(content) == expected


@pytest.mark.parametrize("content", [
    "怎么生成 Word 和 Excel？", "如何做 15 页 PPT？", "不要生成 Word 和 Excel", "停止制作 15 页 PPT",
    "是否能生成 Word？", "能生成Word吗", "可以生成Word吗", "支持Excel导出吗", "能否制作15页PPT",
    "请解释生成Word的方法", "教我制作15页PPT", "了解Word和Excel的区别", "如果需要就生成Word",
    "Word和Excel有什么区别", "Can you generate Word?", "How to generate a PPT?", "Don't generate Word and Excel",
])
def test_inquiries_negation_and_hypotheticals_are_not_execution_requests(content):
    assert requested_outputs(content) == {}


@pytest.mark.parametrize(("content", "expected"), [
    ("不要Word，给我Excel", {"xlsx": True}),
    ("不要生成Excel，帮我制作Word", {"docx": True}),
    ("不要Word给我Excel", {"xlsx": True}),
    ("不需要Word但要Excel", {"xlsx": True}),
    ("不需要Word和Excel，只做PPT", {"pptx": True}),
    ("生成Word和Excel，Word不要了", {"xlsx": True}),
    ("生成Word，Excel也不需要了", {"docx": True}),
    ("生成Word但不要Excel", {"docx": True}),
    ("不要帮我生成Word和Excel", {}),
    ("不需要讲解直接给我Excel", {"xlsx": True}),
    ("生成Word，Word不要封面", {"docx": True}),
    ("请绘制饼图和柱状图，不要饼图", {"chart": True, "chart_types": ["bar"]}),
    ("请绘制饼图，不要饼图", {}),
    ("无需再次查询数据库，导出Word", {"docx": True}),
    ("怎么生成Word？请给我Excel", {"xlsx": True}),
])
def test_negation_is_scoped_to_its_deliverable(content, expected):
    assert requested_outputs(content) == expected


@pytest.mark.parametrize(("content", "expected"), [
    ("把Excel转换成Word", {"docx": True}),
    ("根据Excel生成Word", {"docx": True}),
    ("读取Excel后生成15页PPT", {"pptx": 15}),
    ("做15页Word和20页PPT", {"docx": True, "pptx": 20}),
    ("生成15到20页PPT", {"pptx": True}),
    ("生成大约15页PPT", {"pptx": True}),
])
def test_source_files_and_non_exact_page_counts_do_not_create_false_requirements(content, expected):
    assert requested_outputs(content) == expected


def test_original_inspection_request_requires_all_artifacts():
    content = ("帮我分析一下设备巡检，近期有多少设备需要巡检，但是未巡检的需要图饼并导出为word和excel，"
               "再帮我做一份15页的ppt然后做一份数字大屏放到word里面")
    assert requested_outputs(content) == {
        "chart": True, "chart_types": ["pie"], "docx": True, "xlsx": True,
        "pptx": 15, "dashboard": True, "embedded_dashboard": True,
    }
