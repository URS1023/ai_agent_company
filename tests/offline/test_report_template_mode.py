"""Screen defaults reference templates without changing single-widget contracts.

中文说明：大屏默认"参考模板"，同时不改变单组件查询的既有契约（两种模式互不干扰）。
隔离方式：直接校验 GenerationInput/ScreenGenerationInput 的 schema 行为，不连库、不调用模型。
"""
from app.modules.reports.schemas import GenerationInput, ScreenGenerationInput


def test_screen_defaults_to_auto_reference_and_retains_explicit_modes():
    assert ScreenGenerationInput(theme="财务和设备").template_mode == "auto"
    assert GenerationInput(requirement="设备总数").template_mode == "none"
    manual = ScreenGenerationInput(theme="财务和设备", templateMode="manual", templateId="template-48")
    assert manual.template_mode == "manual"
    assert manual.template_id == "template-48"
    assert ScreenGenerationInput(theme="旧集成", templateMode="none").template_mode == "none"
