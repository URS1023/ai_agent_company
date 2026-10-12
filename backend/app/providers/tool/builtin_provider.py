# -*- coding: utf-8 -*-
"""Builtin provider — system tools always available to the runtime.

中文补充（内置工具 provider）：
- 职责：声明系统自带工具（builtin_specs）并实现其执行（invoke）。这些工具不走
  连接器/密钥链路，而是直接调用平台内部服务，是模型触达平台能力的通道。
- 关键依赖：业务服务（business_ai / analytics / alerts / workflows / presentation /
  artifact / files / knowledge / connector）全部在执行时按需 import，
  避免 provider 与业务模块形成导入环。
- 权限：每个工具在 ``_tool(permissions=[...])`` 中声明所需权限码，由 ToolGateway
  在执行前逐项校验；本模块不重复鉴权，但也不得申明得比实际需要更宽。
- 只读与写入边界：查询类工具只读（不写业务表、不重跑工作流）；写类工具
  （write_file / rename_file / delete_file / add_tool / render_* / fill_template）
  会落库或产生工件；add_tool 等于把外部 HTTP 接口注册成新工具，需要连接器权限
  并经过发布流程，不能作为绕过连接器治理的旁路。
- 多数工具依赖 ctx 中的 workspace_id / user_id / run_id（见 ``_ctx_keys``）；
  run_id 缺失时工件类工具直接报错，不做降级写入。
"""
from __future__ import annotations

import re

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

from ...config import settings
from ...errors import ToolExecutionError
from ...modules.presentation.tool_schema import staged_slide_write_schema
from .base import ToolSpec, ToolResult
from .normalizer import normalize

logger = logging.getLogger("builtin-tools")
# 图片识别结果缓存：key = 文件哈希:模型名:图片数。只缓存"已识别文本"，不缓存
# 图片数据本身；容量上限 128，超出后按插入顺序淘汰最早一项（见 _read_file_images）。
_VISION_TEXT_CACHE: dict[str, tuple[str, int, list[str]]] = {}
_VISION_CACHE_LOCK = threading.Lock()


def _presentation_quality_issues(spec: dict) -> list[str]:
    """Reject structurally sparse pages before they become empty-looking slides.

    中文补充：渲染前的"内容密度"闸门，返回问题列表（为空表示可渲染）。
    按布局分别设下限：content/agenda/cards/metrics ≥3 项、timeline ≥4 步、
    matrix 需 4 个象限、table 至少 2 行数据、chart 的分类与每个序列长度必须一致、
    comparison/two_column 两侧各 ≥2 条且不能用"方案A/左侧"这类占位标题、
    statement 页结论需 ≥10 字。此外限制 statement 页最多占全篇 1/8，
    防止出现"金句堆砌"的空壳 PPT。本函数只读检查，不写库、不渲染。
    """
    from ...modules.presentation.engine import _infer_layout, _item_text

    issues: list[str] = []

    def values_of(value) -> list:
        if isinstance(value, list):
            flattened = []
            for item in value:
                flattened.extend(values_of(item))
            return flattened
        if isinstance(value, dict):
            nested = value.get("items") or value.get("content")
            if isinstance(nested, (list, dict)):
                recovered = values_of(nested)
                if recovered:
                    return recovered
            text = _item_text(value).strip()
            return [text] if text else []
        if isinstance(value, str):
            return [line for line in value.splitlines() if line.strip()]
        if value not in (None, ""):
            return [str(value)]
        return []

    for index, slide in enumerate(spec.get("slides") or [], start=1):
        slide = slide or {}
        layout = _infer_layout(slide)
        items = list(slide.get("items") or slide.get("content") or [])
        title = str(slide.get("title") or f"第 {index} 页")[:28]
        visible_items = [
            _item_text(item).strip() for item in items if _item_text(item).strip()
        ]
        if layout in {"content", "agenda", "cards", "metrics"} and len(visible_items) < 3:
            issues.append(f"《{title}》需要至少 3 个带说明的内容项")
        elif layout == "timeline" and len(visible_items) < 4:
            issues.append(f"《{title}》时间线需要至少 4 个步骤")
        elif layout == "matrix" and len(items) < 4:
            issues.append(f"《{title}》矩阵需要 4 个象限内容")
        elif layout == "chart":
            chart = next((item for item in items if isinstance(item, dict) and item.get("type") == "chart"), slide.get("chart") or {})
            series = chart.get("series") or [] if isinstance(chart, dict) else []
            categories = chart.get("categories") or [] if isinstance(chart, dict) else []
            series_values = []
            for row in series:
                if isinstance(row, dict):
                    values = row.get("values")
                    if values is None:
                        values = row.get("data")
                else:
                    values = row
                series_values.append(values if isinstance(values, list) else [])
            if (
                len(categories) < 2
                or not series_values
                or any(len(values) != len(categories) for values in series_values)
            ):
                issues.append(f"《{title}》缺少完整的图表分类或数据序列")
        elif layout == "table":
            table = next((item for item in items if isinstance(item, dict) and item.get("type") == "table"), {})
            if len(table.get("rows") or []) < 3:
                issues.append(f"《{title}》表格需要表头和至少 2 行数据")
        elif layout in {"comparison", "two_column"}:
            nested = items[0] if len(items) == 1 and isinstance(items[0], dict) else {}
            left = slide.get("left") or nested.get("left") or []
            right = slide.get("right") or nested.get("right") or []
            if len(values_of(left)) < 2 or len(values_of(right)) < 2:
                issues.append(f"《{title}》左右两侧各需要至少 2 条具体内容")
            generic_titles = {
                "", "方案a", "方案b", "左侧", "右侧", "左侧要点", "右侧要点",
                "patha", "pathb", "optiona", "optionb",
            }
            if layout == "two_column":
                column_titles = slide.get("column_titles") or []
                left_heading = (
                    column_titles[0] if len(column_titles) > 0
                    else slide.get("left_title")
                )
                right_heading = (
                    column_titles[1] if len(column_titles) > 1
                    else slide.get("right_title")
                )
            else:
                left_heading = slide.get("left_title")
                right_heading = slide.get("right_title")
            left_title = str(left_heading or "").replace(" ", "").casefold()
            right_title = str(right_heading or "").replace(" ", "").casefold()
            if left_title in generic_titles or right_title in generic_titles:
                label = "栏目" if layout == "two_column" else "对比"
                issues.append(f"《{title}》需要明确左右{label}标题，不能使用方案 A/方案 B")
        elif layout == "statement":
            thesis = str(slide.get("statement") or slide.get("key_message") or "").strip()
            if len(thesis) < 10:
                issues.append(f"《{title}》核心判断需要补充原因、影响或证据")
    statement_count = sum(
        1 for slide in spec.get("slides") or []
        if _infer_layout(slide or {}) == "statement"
    )
    statement_limit = max(1, len(spec.get("slides") or []) // 8)
    if statement_count > statement_limit:
        issues.append(
            f"statement 结论页过多（{statement_count} 页）；最多 {statement_limit} 页，其余改用卡片、对比、表格或流程"
        )
    return issues


def _ctx_keys(ctx) -> dict:
    """从执行上下文里挑出内置工具需要的四个字段（工作区/用户/运行/数据库会话）。

    集中在一处是为了让各工具实现不直接读 ctx 的其它键（如 secret_values、
    数据源配置），避免内置工具意外接触连接器密钥；新增依赖时在此显式添加。
    """
    return {
        "workspace_id": ctx.get("workspace_id"),
        "user_id": ctx.get("user_id"),
        "run_id": ctx.get("run_id"),
        "db": ctx.get("db"),
    }


def _tool(name, description, input_schema, risk="low", provider="builtin", timeout_seconds=120,
          permissions=None, output_schema=None, requires_approval=False) -> ToolSpec:
    """构造内置工具声明；``permissions`` 是执行期必须校验的权限码列表。

    默认值为"低风险、内置 provider、120 秒、无权限要求"；需要权限的工具必须显式
    传入，否则网关的 require_permission 阶段会把该工具当作无需权限的公开能力。
    """
    return ToolSpec(name=name, description=description, provider=provider,
                    input_schema=input_schema, output_schema=output_schema or {},
                    permissions=list(permissions or []), risk_level=risk,
                    timeout_seconds=timeout_seconds, requires_approval=requires_approval)


def builtin_specs() -> List[ToolSpec]:
    """返回全部内置工具声明（每次调用重建，调用方不要缓存返回值）。

    description 会原样进入模型的工具列表，因此这里的措辞同时承担"何时该调用、
    何时不该调用、禁止做什么"的提示职责；input_schema 用
    additionalProperties=False 收紧参数面，越界键名由 validator 直接拒绝。
    修改任何 description 或 schema 都会改变模型行为，属于对外契约变更。
    """
    return [
        _tool('export_inspection_report', '导出平台已保存的巡检逐项证据，生成宋体 Word、中文检查项图表，可同时生成看板。仅用于已有检查报告的检查项分析；应检/未检设备数量须先用query_business_data核对巡检计划和实际记录，再用render_chart_image、export_query_spreadsheet导出。此工具不提供设备巡检计划覆盖率，不把待确认项当未检台数。',
            {'type':'object','additionalProperties':False,'properties':{
                'reference':{'type':'string','maxLength':26},'keyword':{'type':'string','maxLength':200},
                'start':{'type':'string'},'end':{'type':'string'},'include_dashboard':{'type':'boolean'}}},
            permissions=['workflows.view','alerts.view']),
        _tool('query_workflow_results', '读取平台内部已经保存的质检、巡检、保养、维修工作流报告。查看质检数据、设备检查结果或指定工作流/运行编号时优先使用；页面与对话共享同一份结果，不查询外部数据库、不重新运行。',
              {'type': 'object', 'additionalProperties': False, 'properties': {
                  'kind': {'type': 'string', 'enum': ['quality', 'alert'], 'description': '设备巡检、保养、维修为 alert；产品质检为 quality'},
                  'reference': {'type': 'string', 'maxLength': 26, 'description': '工作流或运行标识；预警报告/问题的处置进展使用view_device_alerts的report_id/problem_id，不要混用标识'},
                  'keyword': {'type': 'string', 'maxLength': 200},
                  'category': {'type': 'string', 'enum': ['inspection', 'maintenance', 'repair'], 'description': '设备检查类型：巡检、保养、维修'},
                  'quality_stage': {'type': 'string', 'enum': ['incoming', 'process', 'finished'], 'description': '进厂、过程、成品质检；用户指定阶段时必须传入，成品为 finished'},
                  'history': {'type': 'boolean', 'description': '查历史或历次记录时必须为 true，保留同一工作流的多次运行；keyword 传设备名称'},
                  'start': {'type': 'string', 'description': 'ISO 时间；未带时区按北京时间 +08:00'}, 'end': {'type': 'string', 'description': 'ISO 时间；未带时区按北京时间 +08:00'},
                  'offset': {'type': 'integer', 'minimum': 0, 'maximum': 100000, 'description': '使用上页返回的 nextOffset'},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, 'required': ['kind']},
              permissions=['workflows.view']),
        _tool("list_dashboard_templates", "查询真正可用的大屏模板；可按深色、科技风、财务等关键词筛选，返回名称、模板标识和预览。",
              {"type": "object", "additionalProperties": False, "properties": {
                  "keyword": {"type": "string", "maxLength": 200}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}},
              permissions=["templates.view"]),
        _tool("get_business_dashboard", "查看当前对话最近创建或修改的大屏；无需让用户查找标识。也可指定有权访问的 report_id。",
              {"type": "object", "additionalProperties": False, "properties": {"report_id": {"type": "string", "maxLength": 26}}},
              permissions=["dashboards.view", "datasources.view"]),
        _tool("update_business_dashboard", "将当前对话中的已有大屏调整为深色科技风，保留指标、布局和真实数据绑定；直接保存新修订，不需要重新取数或重新生成。",
              {"type": "object", "additionalProperties": False, "properties": {
                  "report_id": {"type": "string", "maxLength": 26}, "theme": {"type": "string", "enum": ["dark-tech"]}}},
              permissions=["dashboards.view", "dashboards.edit", "datasources.view"]),
        _tool(
            "collect_device_monitoring_snapshot",
            "设备工作流专用：按明确设备范围读取巡检、保养、维修及源规则，生成有界只读快照；不调用模型、不写业务库。",
            {"type": "object", "additionalProperties": False, "required": ["datasource_id", "input"],
             "properties": {"datasource_id": {"type": "string", "maxLength": 26},
                            "input": {"type": "object"},
                            "category": {"type": "string", "enum": ["inspection", "maintenance", "repair", "all"]}}},
            permissions=["analytics.execute", "alerts.execute"],
        ),
        _tool(
            "list_business_data_sources",
            "发现当前用户已授权且启用的数据库及固定 HTTP/API 数据源和字段说明。"
            "当业务目标可能对应多个数据源、用户询问可用数据，或生成大屏前需要缩小范围时调用。"
            "不会测试连接、返回密钥或默认选择第一项。",
            {"type": "object", "additionalProperties": False, "properties": {
                "keyword": {"type": "string", "maxLength": 200, "description": "可选的财务、设备等业务关键词"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "候选上限，默认 8"},
            }},
            permissions=["datasources.view"],
            output_schema={"type": "object", "description": "resource_resolution 资源候选"},
        ),
        _tool(
            "query_business_data",
            "在一个明确授权的数据源中执行数据库自然语言分析、只读 SQL，或固定 HTTP/API 只读采集。"
            "优先绑定 AI 输入框已选择的数据源；不会复用某个数据库连接器去查询另一个库的表。"
            "DSH对话由当前Agent规划：先用schema读取授权元数据，再以sql模式提交明确SQL及task本次取数子任务；工具只校验执行，不调用第二个模型改写。"
            "自然语言转SQL仅供独立分析页面；DSH数据库查询传natural_language会返回明确可修正错误。需要了解相关表时使用schema模式，禁止查询information_schema。"
            "日期按任务服务端日期归一；最近/过去N天为含当天的1–366个自然日，周从周一开始，季度为自然季度；schema回执executionScope会列出可验证范围。"
            "查询零行且需核实数据覆盖时，用profile模式和schema中的明确table_name、date_column，固定只查该表记录数、空日期数、最早/最晚日期；"
            "这是覆盖诊断，不是业务金额统计，不改变正式查询的原始月份、部门等范围。不要反复改SQL扩大时间范围。"
            "查看财务和订单等概览需求请一次传入完整问题，overview 模式按收款、付款、发票、合同、订单等主题返回有限样本和覆盖范围。"
            "HTTP/API 只执行管理员保存且允许自动调用的固定请求，不接受临时 URL。",
            {"type": "object", "additionalProperties": False, "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 30000,
                          "description": "业务问题或只读 SQL"},
                "task": {"type": "string", "maxLength": 8000,
                         "description": "DSH sql模式必填：本次明确取数步骤，例如指定月份应收明细；完整原话和会话由服务端保留"},
                "datasource_id": {"type": "string", "maxLength": 26,
                                  "description": "多选时必填，且必须来自本次选择或发现结果"},
                "mode": {"type": "string", "enum": ["natural_language", "sql", "schema", "overview", "profile"],
                         "description": "DSH使用schema→sql或确定性profile；sql直接执行当前Agent明确SQL，不隐式改写；profile只核对整体日期覆盖"},
                "scope": {"type": "object", "additionalProperties": False, "properties": {
                    "request_quote": {"type": "string", "maxLength": 4000, "description": "复合请求多个月份或部门时，逐字引用包含当前步骤完整范围的原始子句"},
                    "time": {"type": "object", "additionalProperties": False, "properties": {
                        "table": {"type": "string"}, "column": {"type": "string"}}, "required": ["table", "column"]},
                    "department": {"type": "object", "additionalProperties": False, "properties": {
                        "table": {"type": "string"}, "column": {"type": "string"}}, "required": ["table", "column"]},
                }, "description": "需要明确字段映射时绑定schema实际表/列；日期值及部门值仍从原话校验，不能用此参数扩大范围"},
                "table_name": {"type": "string", "maxLength": 256,
                               "description": "profile模式必填，schema实际返回的单个授权表名"},
                "date_column": {"type": "string", "maxLength": 256,
                                "description": "profile模式必填，该表实际存在的DATE/DATETIME/TIMESTAMP日期列"},
                "session_id": {"type": "string", "maxLength": 120},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
            }, "required": ["query"]},
            permissions=["analytics.execute", "datasources.view"],
            output_schema={"type": "object", "description": "真实查询结果、SQL、统计和图表建议"},
            timeout_seconds=180,
        ),
        _tool(
            "view_device_alerts",
            "读取当前用户真正有权查看的持久化设备预警报告与问题列表。"
            "用户说“查看设备预警”“有哪些设备问题”时必须调用；跟进某个问题时传入已有结果中的problem_id，读取实际处置日志。"
            "报告issues表示发现问题，completed仅表示流程执行完成，都不表示问题已处置。不会请求设备、重跑流程或修改状态。"
            "需要预警 Excel 时同时传 export_name，服务端直接导出本次报告、问题和来源，无需读取全文后手抄表格；导出范围仍受分页限制。",
            {"type": "object", "additionalProperties": False, "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "最近报告和问题条数，默认 20"},
                "offset": {"type": "integer", "minimum": 0, "maximum": 10000, "description": "分页起点；指定问题时用于处置日志分页"},
                "reports_offset": {"type": "integer", "minimum": 0, "maximum": 10000, "description": "报告列表独立分页位置，使用 reportsPage.nextOffset 继续"},
                "problems_offset": {"type": "integer", "minimum": 0, "maximum": 10000, "description": "问题列表独立分页位置，使用 problemsPage.nextOffset 继续"},
                "problem_id": {"type": "string", "minLength": 26, "maxLength": 26, "description": "已有问题标识；返回该问题状态、说明和真实处置历史，不用同名巡检报告推断处置"},
                "report_id": {"type": "string", "minLength": 26, "maxLength": 26, "description": "预警报告标识，限定该报告与问题；不是工作流标识"},
                "export_name": {"type": "string", "minLength": 1, "maxLength": 200, "description": "可选 Excel 文件名，例如设备预警信息表.xlsx；直接导出本页已授权结果，包含报告、问题、来源与分页说明"},
            }},
            permissions=["alerts.view"],
            output_schema={"type": "object", "description": "alert_problem_list 交互结果"},
        ),
        _tool(
            "create_business_dashboard",
            "根据当前用户授权的数据目录、真实表字段和逐组件只读查询，生成并保存一个可编辑数字大屏草稿。"
            "用户说“生成财务和设备数字大屏”等业务目标时调用；不要求用户提供路径或 ID。"
            "默认 auto 是动态适配而非固定默认模板；manual 必须明确 template_id，none 明确不用模板。"
            "原生 DSH 对话应先完成 schema/sql 取数，再传 query_ids；直接复用成功结果绑定模板，不重新查询或调用设计模型。"
            "只创建草稿，不公开发布、不写生产表、不用演示数据兜底。",
            {"type": "object", "additionalProperties": False, "properties": {
                "requirement": {"type": "string", "minLength": 2, "maxLength": 40000,
                                "description": "完整业务目标、指标、时间范围和展示要求"},
                "title": {"type": "string", "maxLength": 200, "description": "可选草稿名称"},
                "query_ids": {"type": "array", "minItems": 1, "maxItems": 8,
                              "items": {"type": "string"}, "description": "本轮已成功查询的标识，原样保存并绑定所选模板"},
                "bindings": {"type": "array", "maxItems": 8, "items": {"type": "object", "additionalProperties": False,
                    "properties": {"query_id": {"type": "string"}, "value_field": {"type": "string"},
                                   "label_field": {"type": "string"}}, "required": ["query_id", "value_field"]},
                    "description": "可选：为各查询明确选择真实数值字段与维度字段，每份查询一个绑定，不填虚构指标或单位"},
                "datasource_ids": {"type": "array", "maxItems": 8, "items": {"type": "string"},
                                       "description": "仅填写 list_business_data_sources 实际返回的标识；不确定时留空，由系统发现授权目录"},
                "template_mode": {"type": "string", "enum": ["auto", "manual", "none"],
                                  "description": "默认 auto；用户明确要求空白画布时用 none"},
                "template_id": {"type": "string", "maxLength": 100,
                                "description": "仅 manual 模式填写的明确模板标识"},
                "width": {"type": "integer", "minimum": 320, "maximum": 16384},
                "height": {"type": "integer", "minimum": 200, "maximum": 16384},
            }, "required": ["requirement"]},
            # The conversational action defaults to auto template matching.
            # Direct API callers may still explicitly use template_mode=none,
            # while the AI tool remains permission-safe for its default path.
            permissions=[
                "dashboards.view", "dashboards.create", "dashboards.execute",
                "datasources.view", "templates.view",
            ],
            output_schema={"type": "object", "description": "dashboard 持久化草稿结果"},
            timeout_seconds=300,
        ),
        _tool("list_business_skills", "列出本项目已安装的业务技能与可按需读取的资源文件；不会执行技能脚本。",
              {"type": "object", "properties": {}, "additionalProperties": False}),
        _tool("read_business_skill_resource", "读取已安装业务技能的SKILL.md或references文本资源，用于确认口径和工具参数；只能读取该技能包内文件。",
              {"type": "object", "additionalProperties": False, "properties": {
                  "skill_name": {"type": "string", "maxLength": 100},
                  "path": {"type": "string", "maxLength": 300, "description": "技能包内相对路径，如references/tool-contracts.md"}},
               "required": ["skill_name", "path"]}),
        _tool("current_datetime", "返回服务器本地日期、时间与明确时区偏移，另附UTC时间；不要把UTC日期当成本地业务日期。",
              {"type": "object", "properties": {}}),
        _tool("python_execute",
              "在受控 Python 沙箱中执行数据分析、计算、统计、图表、Excel/CSV 处理。可用库：pandas、numpy、matplotlib、openpyxl、python-docx。"
              "通过 result 变量返回结构化结果（dict/list/数值/字符串）；请将图片/文件保存到 OUTPUT_DIR 或相对路径，"
              "常见的 /tmp 与 /mnt/data 路径也会自动映射并作为工件收集。"
              "Matplotlib 圆角矩形请使用 FancyBboxPatch 与 boxstyle，Rectangle 不支持 radius 参数。"
              "沙箱默认禁止联网与 subprocess；单次运行有超时限制。",
              {"type": "object",
               "properties": {
                   "code": {"type": "string",
                            "description": "要执行的 Python 代码；最后把结论写入 result 变量（如 result = {...}）"},
                   "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 300, "default": 120,
                                       "description": "执行超时（秒）"},
                   "inputs": {"type": "object", "description": "工作流命名输入；通过 inputs 字典读取，单一默认输入也可用 input。支持 main(**inputs) 返回结果"},
               },
               "required": ["code"]},
              provider="sandbox", timeout_seconds=300),
        _tool("search_knowledge",
              "在指定的企业知识库中检索相关资料，返回最相关的原文片段及其来源文件。"
              "当你需要引用公司资质、历史项目、制度文档等知识时必须调用此工具。",
              {"type": "object",
               "properties": {
                   "query": {"type": "string", "description": "检索问题，应包含具体业务关键词"},
                   "knowledge_base_ids": {"type": "array", "items": {"type": "string"},
                                          "description": "限定检索的知识库ID列表，为空则检索全部"},
                   "top_k": {"type": "integer", "description": "返回片段数量，默认5"},
               },
               "required": ["query"]}, permissions=["knowledge.view"]),
        _tool("read_file",
              "读取用户上传的文件内容（PDF/DOCX/XLSX/CSV/TXT/PPTX），返回可阅读的文本。"
              "默认分页返回，长文件必须使用 offset 继续读取；只查特定信息时优先传 query，"
              "不要重复读取相同 file_id 与 offset。",
              {"type": "object",
               "properties": {
                   "file_id": {"type": "string", "description": "文件ID"},
                   "file_name": {"type": "string", "description": "文件名提示"},
                   "query": {"type": "string", "description": "可选；仅返回包含该关键词的相关片段"},
                   "offset": {"type": "integer", "minimum": 0, "default": 0,
                              "description": "分页起始字符位置，首次读取为0"},
                   "max_chars": {"type": "integer", "minimum": 1000, "maximum": 80000,
                                 "default": 30000, "description": "本次最多返回字符数"},
                   "include_images": {"type": "boolean", "default": True,
                                      "description": "是否识别文件中的图片，默认开启"},
                   "max_images": {"type": "integer", "minimum": 0, "maximum": 8,
                                  "default": 4, "description": "本次最多识别图片数"},
               },
               "required": ["file_id"]}, permissions=["files.view"]),
        _tool("render_document",
              "根据结构化 document_spec 生成 DOCX（推荐）或 PDF 文档工件，返回工件ID供下载。"
              "spec.sections 每个块支持 type: h1..h4/p/bullet/numbered/table/code/image/page_break。",
              {"type": "object",
               "properties": {
                   "document_spec": {"type": "object",
                                     "description": "DocumentSpec: {title, sections:[{title,blocks:[]}]}"},
                   "format": {"type": "string", "enum": ["docx", "pdf"], "default": "docx"},
                   "name": {"type": "string", "description": "文件名（不含扩展名）"},
               },
               "required": ["document_spec"]}),
        _tool("create_presentation_plan",
              "PPT 分阶段生成第 1 步：只创建故事线和正文页大纲，不写整页详细内容。"
              "slides 是正文大纲，每页提供 title、layout、section、key_message。"
              "用户逐页指定主题且没要求独立封面/结束页时，必须设置include_cover=false、include_closing=false，保留每个主题。"
              "例如用户指定总共5页且给出5个主题：设置两个开关false并提供对应5项slides。"
              "默认开关为true兼容旧稿；正文数量=总页数减去实际启用的封面和结束页数量。"
              "同任务不同标题创建独立草稿；记录返回的draft_id，后续写页、导出均显式传该标识。",
              {"type": "object", "properties": {
                  "title": {"type": "string"},
                  "draft_id": {"type": "string", "description": "仅在恢复已有稿时填写该任务回执中的草稿标识；新建稿省略"},
                  "subtitle": {"type": "string"},
                  "author": {"type": "string"},
                  "footer": {"type": "string"},
                  "closing_title": {"type": "string"},
                  "closing_subtitle": {"type": "string"},
                  "template_id": {"type": "string"},
                  "aspect_ratio": {"type": "string", "enum": ["16:9", "4:3"]},
                  "page_count": {"type": ["integer", "string"]},
                  "include_cover": {"type": "boolean", "default": True,
                      "description": "是否额外生成封面；用户已逐页指定全部主题且没要求独立封面时必须false"},
                  "include_closing": {"type": "boolean", "default": True,
                      "description": "是否额外生成结束页；用户已逐页指定全部主题且没要求独立结束页时必须false"},
                  "slides": {"type": "array", "items": {"type": "object", "properties": {
                      "title": {"type": "string"}, "layout": {"type": "string"},
                      "section": {"type": "string"}, "key_message": {"type": "string"},
                  }}},
              }, "required": ["slides"]}),
        _tool("write_presentation_slides",
              "PPT 分阶段生成第 2 步：建议按大纲每次写入 1–4 页详细正文。"
              "slide_number是正文索引；用户指定总稿第N页时优先用physical_page_number=N，或按outline的page_number映射到slide_number。"
              "遗漏页码时系统会按标题和待写顺序补齐，"
              "误提交超过 4 页时系统会自动拆批。必须包含结论、解释和证据；"
              "卡片直接使用 {type:'card',title,description}，不要把 label/data 包进 value 对象；"
              "时间线直接把 4–5 个 {title,description} 步骤放入 items；"
              "图表推荐放入 chart:{chart_type,categories,series:[{name,values}]}，categories 不可遗漏。"
              "表格可使用 {type:'table',headers:[],rows:[]} 或顶层 table。"
              "正文示例：{slide_number:1,layout:'cards',title:'分析',items:[{title:'结论',description:'证据和解释'},...]}。"
              "notes 只是备注，不能代替 items；不得为普通正文传空 series 后省略 items。"
              "封面和结尾是否自动生成取决于大纲开关；读取outline/pages映射、missing_slide_numbers与rejected_slides后继续。"
              "填写create_presentation_plan返回的draft_id以选择目标稿；省略时使用当前任务最后选中的稿，不能杜撰标识。",
              staged_slide_write_schema()),
        _tool("finalize_presentation",
              "PPT 分阶段生成第 3 步：在全部正文页写完后检查内容密度、精确页数并输出 PPTX。"
              "若返回缺页或稀疏页，只需用 write_presentation_slides 重写对应页后再次调用。"
              "若返回presentation_overflow，必须如实说明可见行数与备注行数，不得宣称溢出明细在页面完整展示。",
              {"type": "object", "properties": {
                  "name": {"type": "string", "description": "输出文件名（不含扩展名）"},
                  "draft_id": {"type": "string", "description": "要交付的大纲回执中的draft_id；省略时使用当前稿"},
              }}),
        _tool("render_presentation",
              "兼容性工具：根据完整 PresentationSpec v2 一次生成 PPTX。"
              "普通会话应优先使用 create_presentation_plan → write_presentation_slides → finalize_presentation。"
              "先建立结论先行的故事线，再为每页选择语义布局；不要把所有页都写成项目符号。"
              "支持 layout: statement/agenda/section/content/two_column/cards/"
              "metrics/comparison/timeline/table/chart/quote/image/matrix/closing。"
              "普通内容每页最多6条，卡片或指标最多4项；超出时拆页。"
              "图表项：{type:'chart',chart_type:'column|bar|line|pie|doughnut|area',"
              "categories:[],series:[{name,values}],insight,source}。"
              "指标项：{type:'metric',value,label,title,description}。"
              "正文页必须信息充足：普通页3–6个带解释的要点，卡片/指标3–4项，"
              "对比每侧3–5条，时间线4–5步；禁止只有标题或空框。"
              "必须遵循本次会话选择的 template_id 和 page_count。",
              {"type": "object",
               "properties": {"presentation_spec": {
                                  "type": ["object", "string"],
                                  "properties": {
                                      "title": {"type": "string"},
                                      "subtitle": {"type": "string"},
                                      "author": {"type": "string"},
                                      "footer": {"type": "string"},
                                      "include_closing": {"type": "boolean"},
                                      "include_cover": {"type": "boolean"},
                                      "slides": {
                                          "type": "array",
                                          "items": {
                                              "type": "object",
                                              "properties": {
                                                  "title": {"type": "string"},
                                                  "section": {"type": "string"},
                                                  "key_message": {"type": "string"},
                                                  "statement": {"type": "string", "description": "该页需要突出的一句完整判断"},
                                                  "left_title": {"type": "string"},
                                                  "right_title": {"type": "string"},
                                                  "left": {"type": ["array", "object"], "description": "双栏或对比页左侧，建议3–5条"},
                                                  "right": {"type": ["array", "object"], "description": "双栏或对比页右侧，建议3–5条"},
                                                  "column_titles": {"type": "array", "items": {"type": "string"}},
                                                  "notes": {"type": "string", "description": "可选演讲备注；写自然口语，不重复页面文字"},
                                                  "layout": {
                                                      "type": "string",
                                                      "enum": [
                                                          "statement", "agenda", "section", "content",
                                                          "two_column", "cards", "metrics", "comparison",
                                                          "timeline", "table", "chart", "quote", "image",
                                                          "matrix", "closing",
                                                      ],
                                                  },
                                                  "items": {"type": "array", "description": "普通页优先使用{title,description}；指标使用{type:'metric',value,title,description}；图表和表格使用工具说明中的完整结构"},
                                              },
                                          },
                                      },
                                  },
                              },
                              "template_id": {"type": "string", "description": "PPT 模板 ID，例如 executive-ivory、midnight-analytics、aurora-glass"},
                              "aspect_ratio": {"type": "string", "enum": ["16:9", "4:3"], "default": "16:9"},
                              "page_count": {"type": "integer", "minimum": 3, "maximum": 50, "description": "总页数，包含封面和结束页"},
                              "name": {"type": "string"}},
               "required": ["presentation_spec"]}),
        _tool("render_spreadsheet",
              "根据 spec 生成 XLSX 表格工件。spec: {sheet_name, columns:[], data:[[...]]}",
              {"type": "object",
               "properties": {"spec": {"type": "object"},
                              "name": {"type": "string"}},
                "required": ["spec"]}),
        _tool("query_inventory_data", "直接查询已同步库存、仓库或物料数据，不调用模型生成SQL。"
              "查备品备件、仓库名称、批次和单位优先用此工具。返回可复用queryId，空阈值与不明确单位保持未知。"
              "结构化筛选必须落实用户指定范围；不支持的历史时间/部门/汇总条件改用query_business_data，不得忽略。",
              {"type": "object", "additionalProperties": False, "properties": {
                  "datasource_id": {"type": "string", "maxLength": 26},
                  "kind": {"type": "string", "enum": ["inventory", "warehouses", "materials"]},
                  "warehouse_id": {"type": "string"}, "material_id": {"type": "string"},
                  "name": {"type": "string", "maxLength": 200}, "batch_number": {"type": "string", "maxLength": 200},
                  "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
                  "page_size": {"type": "integer", "minimum": 1, "maximum": 200}}, "required": []},
              permissions=["analytics.execute", "datasources.view"], timeout_seconds=30),
        _tool("export_query_presentation", "将本任务已有query_ids制成PPT，默认report汇报模式展示指标、分组对比和明细预览，完整行列在备注。"
              "只有用户要求全部行列逐页展示时选snapshot。scope_notes填写实际缺失业务与确认的范围；不重新查询、不让模型抄写数字。通用介绍PPT用文档制作工具。",
              {"type": "object", "additionalProperties": False, "properties": {
                  "query_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8},
                  "title": {"type": "string", "minLength": 1, "maxLength": 120},
                  "presentation_mode": {"type": "string", "enum": ["report", "snapshot"], "default": "report"},
                  "scope_notes": {"type": "array", "maxItems": 10, "items": {"type": "string", "minLength": 1, "maxLength": 400}},
                  "name": {"type": "string", "minLength": 1, "maxLength": 200}}, "required": ["query_ids"]},
              permissions=["analytics.export", "datasources.view"]),
        _tool("export_query_spreadsheet", "将本任务成功查询的 query_id 原样导出为宋体 Excel，附数据来源页，"
              "不重查数据库、不需要重新输入表格数据。数据库分析的 Excel 交付优先使用此工具，使用 name 直接指定交付文件名。",
              {"type": "object", "additionalProperties": False, "properties": {
                  "query_id": {"type": "string"},
                  "name": {"type": "string", "minLength": 1, "maxLength": 200,
                           "description": "交付文件名，例如备品备件库存表.xlsx；可省略扩展名"}}, "required": ["query_id"]},
              permissions=["analytics.export", "datasources.view"]),
        _tool("render_chart_image", "把本任务已成功查询的 query_id 渲染为宋体图表图片，不重新查询数据库。"
              "类别数据使用 label_column 和 value_column；一行汇总指标使用 value_columns。饼图必须有非负真实数值。",
              {"type": "object", "additionalProperties": False, "properties": {
                  "query_id": {"type": "string"}, "chart_type": {"type": "string", "enum": ["pie", "bar", "line"]},
                  "title": {"type": "string"}, "label_column": {"type": "string"}, "value_column": {"type": "string"},
                  "value_columns": {"type": "array", "items": {"type": "string"}, "maxItems": 20}},
               "required": ["query_id", "chart_type"]}, permissions=["analytics.export", "datasources.view"]),
        _tool("create_dashboard_from_queries", "把本任务已成功查询的 query_ids 原样创建为可打开的数字大屏。"
              "用于已完成取数后交付大屏；不重复查询、不新增指标，保存查询快照和来源。已手选模板时自动保留模板并绑定兼容槽。",
              {"type": "object", "additionalProperties": False, "properties": {
                  "query_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8},
                  "bindings": {"type": "array", "maxItems": 8, "items": {"type": "object", "additionalProperties": False,
                      "properties": {"query_id": {"type": "string"}, "value_field": {"type": "string"},
                                     "label_field": {"type": "string"}}, "required": ["query_id", "value_field"]}},
                  "title": {"type": "string", "maxLength": 200}}, "required": ["query_ids", "title"]},
              permissions=["dashboards.create", "analytics.view", "datasources.view"]),
        _tool("render_dashboard_image", "将已保存的大屏 report_id 导出为宋体数据快照图片，供 Word image 块引用。"
              "保留真实查询来源、版本和显示范围，不刷新数据库。导出为数据快照，交互页面布局请打开原大屏。",
              {"type": "object", "additionalProperties": False, "properties": {
                  "report_id": {"type": "string"}}, "required": ["report_id"]},
              permissions=["dashboards.view", "datasources.view"]),
        _tool("save_json_artifact", "把结构化数据(JSON)保存为工件，供后续继续分析或用户下载。",
              {"type": "object",
               "properties": {"data": {"type": "object"}, "name": {"type": "string"}},
               "required": ["data"]}),
        _tool("list_files",
              "列出当前用户有权访问的工作区文件（含 id、名称、大小、类型）。需要确认是否已有文件时使用。",
              {"type": "object",
               "properties": {"query": {"type": "string", "description": "按文件名关键字筛选"}}}, permissions=["files.view"]),
        _tool("write_file",
              "新建/覆盖写入一个文件到工作区（用户可下载）。content 直接给文本即可；二进制内容请用 base64。",
              {"type": "object",
               "properties": {
                   "file_name": {"type": "string", "description": "文件名（含扩展名，如 report.txt）"},
                   "content": {"type": "string", "description": "文本内容（与 content_base64 二选一）"},
                   "content_base64": {"type": "string", "description": "base64 编码的二进制内容"},
                   "mime_type": {"type": "string", "description": "MIME 类型，可选"},
               },
               "required": ["file_name"]}),
        _tool("copy_file",
              "复制工作区中一个已存在的文件为新文件（新文件名）。",
              {"type": "object",
               "properties": {"file_id": {"type": "string", "description": "源文件ID"},
                              "new_name": {"type": "string", "description": "新文件名（含扩展名）"}},
               "required": ["file_id", "new_name"]}),
        _tool("rename_file",
              "重命名工作区中的一个文件。",
              {"type": "object",
               "properties": {"file_id": {"type": "string"},
                              "new_name": {"type": "string", "description": "新文件名（含扩展名）"}},
               "required": ["file_id", "new_name"]}),
        _tool("delete_file",
              "删除工作区中的一个文件（软删除，不影响其它会话引用）。",
              {"type": "object",
               "properties": {"file_id": {"type": "string"}},
               "required": ["file_id"]}),
        _tool("add_tool",
              "把企业程序接入为可调用工具：给出工具名、用途、参数结构以及要调用的接口(URL/方法)。"
              "适合把新的 REST API 注册成工具供随后使用。",
              {"type": "object",
               "properties": {
                   "name": {"type": "string", "description": "工具名（英文小写下划线）"},
                   "description": {"type": "string", "description": "工具用途说明"},
                   "url": {"type": "string", "description": "接口地址，如 https://api.example.com/v1/render_ppt"},
                   "method": {"type": "string", "enum": ["GET", "POST"], "default": "POST"},
                   "input_schema": {"type": "object", "description": "参数 JSON Schema（properties/required）"},
               },
               "required": ["name", "description", "url"], "additionalProperties": False},
              permissions=["connectors.create", "connectors.publish"]),
        _tool("fill_template",
              "把解析好的数据填入【用户上传的模板文件】（支持 .xlsx / .docx），严格保留模板原有结构、字体、列宽与格式；"
              "适用场景：用户说“写入/填入到 xxx.xlsx/模板”时，必须先 read_file 了解模板结构，再用本工具填入，禁止新建结构不同的文件。"
              "可选 font 可控制填入内容的字体（name/size/bold/color）。",
              {"type": "object",
               "properties": {
                   "file_id": {"type": "string", "description": "用户上传的模板文件 ID（xlsx/docx）"},
                   "cells": {"type": "object", "description": "按单元格坐标填入：{B4: 值, C2: ...}"},
                   "by_label": {"type": "array", "items": {"type": "object"},
                                "description": "按表头标签填入：[{label: 客户姓名, value: 高君华, direction: right|below}]"},
                   "tables": {"type": "array", "items": {"type": "object"},
                              "description": "在指定起始单元格追加表格行：[{start: A10, rows: [[...]]}]"},
                   "replace": {"type": "object", "description": "（docx）占位符替换：{{名前}} -> 值"},
                   "font": {"type": "object", "description": "可选字体：{name: 宋体, size: 11, bold: false, color: 'D00000'}"},
                   "name": {"type": "string", "description": "输出文件名（缺省：原模板名-已填入.扩展名）"},
               },
               "required": ["file_id"]}),
    ]


def _business_action(name: str, args: dict, ctx: dict) -> ToolResult:
    """Delegate to the same typed services used by the documented v1 APIs.

    中文补充：四个"业务动作型"内置工具的统一实现，复用与 v1 API 相同的 typed
    service（business_ai.service / analytics.queries / alerts 概览），
    保证对话入口与 API 入口的鉴权、数据范围、限额完全一致。

    关键治理点：
    - 数据源必须落在"本次对话显式选择"的范围内（selected_datasource_ids）；
      多选时必须显式传 datasource_id，不能默认取第一个，避免选错库。
    - 自然语言取数优先使用服务端保存的原始用户请求（Run.context_snapshot 的
      user_request）而不是模型改写的子任务，防止模型自行放宽时间/客户范围。
    - 同一轮对话内相同查询键的结果可复用（query_receipts，仅限当前 turn 且
      非截断结果），避免重复取数，但不跨轮次复用。
    - 大屏创建使用与服务端绑定一致的 request_key，模型重试不会产生多个大屏。
    异常：数据源越界、HTTP 数据源要求 SQL 等抛 ValidationError；
    大屏生成失败若为不可重试业务错误，返回 failed 结果而不抛错。
    """
    from ...modules.business_ai.context import auth_context_from_tool
    from ...modules.business_ai.schemas import (
        AlertOverviewInput, DashboardDraftInput, DataSourceDiscoveryInput,
    )
    from ...modules.business_ai.service import (
        alert_overview, create_dashboard_draft, discover_data_sources,
    )

    auth_ctx = ctx.get("auth_context") or auth_context_from_tool(ctx)
    selected_datasource_ids = list(dict.fromkeys(ctx.get("selected_datasource_ids") or []))
    selected_dashboard_template_id = str(
        ctx.get("selected_dashboard_template_id") or ""
    ).strip() or None
    selected_dashboard_template_mode = str(
        ctx.get("selected_dashboard_template_mode") or ""
    )
    if name == "list_business_data_sources":
        view = discover_data_sources(auth_ctx, DataSourceDiscoveryInput(
            keyword=str(args.get("keyword") or ""), limit=int(args.get("limit") or 8),
        ))
    elif name == "query_business_data":
        from ...errors import ValidationError
        from ...modules.analytics.sources import get_source as get_any_source
        from ...modules.analytics.queries import execute_query
        from ...modules.analytics.schemas import QueryInput
        datasource_id = str(args.get("datasource_id") or "").strip()
        if not datasource_id:
            if len(selected_datasource_ids) != 1:
                raise ValidationError(
                    "请选择一个数据源后再查询；本次多选时需要明确 datasource_id",
                    selected_datasource_ids=selected_datasource_ids,
                )
            datasource_id = selected_datasource_ids[0]
        if selected_datasource_ids and datasource_id not in selected_datasource_ids:
            raise ValidationError("查询数据源不在本次明确选择范围内")
        source = get_any_source(auth_ctx, datasource_id, active=True, provider="any")
        from ...modules.analytics.dsh_query_execution import is_dsh_query, execute_dsh_query
        dsh_execution = is_dsh_query(auth_ctx, ctx)
        if str(args.get("mode") or "natural_language") == "profile":
            from ...modules.analytics.data_profile import profile_data_coverage
            return normalize({"status": "success", "data": profile_data_coverage(
                auth_ctx, source, str(args.get("table_name") or ""), str(args.get("date_column") or ""),
                run_id=ctx.get("run_id"),
            )}, tool_name=name)
        if str(args.get("mode") or "natural_language") == "schema":
            from ...modules.analytics.business_catalog import business_schema
            schema_view = business_schema(auth_ctx, source, str(args.get("query") or ""))
            if dsh_execution:
                from ...modules.analytics.query_intent import query_context
                from ...modules.analytics.dsh_query_execution import required_scope, ScopeValidationError
                intent = query_context(auth_ctx, ctx, datasource_id, str(args.get("task") or args.get("query") or ""))
                if intent:
                    try:
                        schema_view["executionScope"] = required_scope(intent, args.get("scope") or {})
                    except ScopeValidationError as exc:
                        schema_view["executionScope"] = {"status": "needs_binding", "message": exc.message, **exc.extra}
            return normalize({"status": "success", "data": schema_view}, tool_name=name)
        if source.provider == "http":
            if str(args.get("mode") or "natural_language") == "sql":
                raise ValidationError("HTTP/API 数据源不执行 SQL；请使用自然语言模式读取已保存的固定接口")
            from ...modules.collection.execution import collect
            result = collect(
                auth_ctx, datasource_id, automatic=True, permission="analytics.execute",
                max_rows=int(args.get("max_rows") or 100),
                query_text=str(args.get("query") or ""),
            )
            return normalize({"status": "success", "data": result.model_dump(mode="json")}, tool_name=name)
        from ...modules.analytics.business_overview import execute_overview, wants_overview
        mode = str(args.get("mode") or "natural_language")
        if dsh_execution:
            if mode == "overview":
                from ...modules.analytics.query_intent import query_context
                original_intent = query_context(auth_ctx, ctx, datasource_id, str(args.get("task") or args.get("query") or ""))
                if not original_intent or not wants_overview(original_intent["currentRequest"]):
                    return normalize({"status": "failed", "error_code": "dsh_overview_scope_unsupported",
                        "error_summary": "原始需求含具体业务条件，固定概览无法保证完整范围；请用schema与sql模式执行明确取数步骤。",
                        "data": {"supportedModes": ["schema", "sql", "profile"]},
                        "metadata": {"recovery": "correct_input", "business_data_queried": False}}, tool_name=name)
                return normalize({"status": "success", "data": execute_overview(auth_ctx, source, original_intent["currentRequest"])}, tool_name=name)
            return normalize(execute_dsh_query(auth_ctx, ctx, source, args), tool_name=name)
        overview_question = str(args.get("query") or "")
        if mode == "natural_language" and ctx.get("run_id"):
            from ...models import Run
            invocation = auth_ctx.db.get(Run, ctx["run_id"])
            if (invocation and invocation.workspace_id == auth_ctx.workspace_id
                    and invocation.started_by_user_id == auth_ctx.user_id):
                original = (invocation.context_snapshot or {}).get("user_request")
                if isinstance(original, str) and original.strip():
                    # The model may add dates/customer fields to a simple topic
                    # request. Those additions must not bypass bounded overview
                    # routing or change the user's requested business scope.
                    overview_question = original
        if mode == "overview" or (mode == "natural_language" and wants_overview(overview_question)):
            return normalize({"status": "success", "data": execute_overview(auth_ctx, source, overview_question)}, tool_name=name)
        intent = None
        explicit_sql = None
        redirected_sql = False
        if mode in {'natural_language', 'sql'}:
            from ...modules.analytics.query_intent import explicit_user_sql, query_context
            intent = query_context(auth_ctx, ctx, datasource_id, str(args.get('query') or ''))
            if intent and mode == 'sql':
                explicit_sql = explicit_user_sql(intent['currentRequest'])
                if explicit_sql is None:
                    # A planner-written SQL string must not bypass the original
                    # user scope and follow-up history applied by natural SQL.
                    mode = 'natural_language'
                    redirected_sql = True
        if intent and mode == 'natural_language' and explicit_user_sql(str(args.get('query') or '')):
            redirected_sql = True
        scope_metadata = {}
        cache_key, control = None, None
        if intent and mode == 'natural_language':
            from ...modules.analytics.connections import source_version
            from ...modules.runtime.task_control import current
            control = current.get()
            cache_key = (auth_ctx.workspace_id, auth_ctx.user_id, intent['runId'],
                         datasource_id, source_version(source), intent['currentRequest'],
                         intent.get('plannerTask', '').strip())
            scope_metadata = {
                'user_scope_applied': True, 'requested_mode': 'sql' if redirected_sql else 'natural_language', 'executed_mode': 'natural_language',
                'query_purpose': 'business_result', 'planner_sql_replanned': redirected_sql,
                'guidance': '本次按服务端保存的完整原始用户要求查询，模型自行改写的子任务或SQL没有替代原始要求。'
                            '0条或COUNT=0是有效结果，不能去掉原筛选条件或改查已删除记录来凑出结果。'
                            '结果范围以generatedSql实际执行的筛选为准，不能把提交SQL的范围当成已执行范围。'
                            '若需核实该表完整日期覆盖，使用profile模式及明确table_name/date_column，不要反复改SQL扩大范围。'
                            '请根据本次实际查询值回答；确需元数据时使用schema模式，不要重复变换SQL重查同一目标。',
            }
            cached = control.query_receipts.get(cache_key) if control else None
            if cached and not cached.get('truncated') and len(cached.get('data') or []) <= int(args.get('max_rows') or 100):
                from copy import deepcopy
                from ...common.authorization import require_permission
                require_permission(auth_ctx, 'analytics.execute')
                # Source access and server-owned run identity were rechecked
                # above. This complete result belongs only to this active turn.
                return normalize({'status': 'success', 'data': deepcopy(cached),
                    'metadata': {**scope_metadata, 'same_turn_result_reused': True}}, tool_name=name)
        result = execute_query(auth_ctx, QueryInput(
            datasourceId=datasource_id,
            query=(intent['currentRequest'] if intent and mode == 'natural_language'
                   else explicit_sql if explicit_sql is not None else str(args.get("query") or "")),
            mode=mode,
            sessionId=intent['conversationId'] if intent else str(args.get("session_id") or ctx.get("conversation_id") or ""),
            maxRows=int(args.get("max_rows") or 100),
        ), **({'user_intent': intent} if intent else {}))
        result_data = result.model_dump(mode="json")
        if cache_key and control and not result_data.get('truncated') and len(control.query_receipts) < 8:
            import json
            from copy import deepcopy
            if len(json.dumps(result_data, ensure_ascii=False)) <= 100000:
                control.query_receipts[cache_key] = deepcopy(result_data)
        return normalize({"status": "success", "data": result_data, 'metadata': scope_metadata}, tool_name=name)
    elif name == "view_device_alerts":
        view = alert_overview(auth_ctx, AlertOverviewInput(limit=int(args.get("limit") or 20),
            offset=int(args.get("offset") or 0), reports_offset=args.get("reports_offset"), problems_offset=args.get("problems_offset"), problem_id=args.get("problem_id"), report_id=args.get("report_id")))
        if args.get("export_name") is not None:
            from ...modules.chat_artifacts.alert_spreadsheet import export_alert_spreadsheet
            return export_alert_spreadsheet(auth_ctx, ctx, view.model_dump(mode="json"), args["export_name"])
    else:
        # One conversational Run delivers at most one dashboard.  Keep the key
        # stable across model retries/argument refinements; direct API callers
        # retain their explicit requestKey contract in the service endpoint.
        request_key = "chat_" + str(ctx.get("run_id") or "direct") + "_dashboard"
        requested_ids = list(args.get("datasource_ids") or [])
        if selected_datasource_ids:
            requested_ids = selected_datasource_ids
        requirement = str(args.get("requirement") or "")
        # The chat model describes the tool call; it cannot redefine the user's
        # dashboard scope on every retry. Resolve the server-owned user turn.
        from ...models import Run, Message
        from sqlalchemy import select
        original = ""
        invocation = auth_ctx.db.get(Run, ctx.get("run_id")) if ctx.get("run_id") else None
        if invocation and invocation.workspace_id == auth_ctx.workspace_id and invocation.started_by_user_id == auth_ctx.user_id:
            original = (invocation.context_snapshot or {}).get("user_request")
            if not original:
                original = auth_ctx.db.scalar(select(Message.content).where(
                    Message.workspace_id == auth_ctx.workspace_id,
                    Message.conversation_id == invocation.conversation_id,
                    Message.role == "user", Message.created_at <= invocation.created_at,
                ).order_by(Message.sequence.desc()).limit(1))
            if original:
                from ...modules.business_ai.dashboard_actions import grounded_requirement
                requirement = grounded_requirement(auth_ctx, invocation, str(original))
        if not requested_ids and invocation:
            from ...modules.business_ai.dashboard_actions import queried_datasource_ids
            requested_ids = queried_datasource_ids(auth_ctx, invocation)
        template_mode = str(args.get("template_mode") or "auto")
        template_id = args.get("template_id")
        from ...modules.reports.templates import list_templates
        named_templates = [item for item in (list_templates(auth_ctx) if re.search(r"模板|换成|改用|使用", str(original)) else [])
                           if item.get("enabled", True) and item["name"] in str(original)
                           and re.search(r"模板|换成|改用|使用|用", str(original))]
        named_templates.sort(key=lambda item: len(item["name"]), reverse=True)
        style_change = bool(re.search(r"(?:深色|暗色|科技风).*(?:模板|重新)|(?:模板|重新).*(?:深色|暗色|科技风)", str(original or requirement)))
        if named_templates:
            template_mode, template_id = "manual", named_templates[0]["id"]
        elif style_change:
            from ...modules.business_ai.dashboard_actions import template_candidates
            matches = template_candidates(auth_ctx, "深色 科技", 1)
            if matches:
                template_mode, template_id = "manual", matches[0]["id"]
        elif selected_dashboard_template_id:
            # The user's explicit layout request, rather than a model argument,
            # may release the selected template's fixed structure.
            if re.search(r"自适应|自由布局|自动布局|auto\s*模式", str(original or requirement), re.I):
                template_mode = "auto"
                template_id = None
            else:
                template_mode = "manual"
                template_id = selected_dashboard_template_id
        elif template_id and template_mode == "manual":
            from ...modules.reports.templates import get_template
            get_template(auth_ctx, template_id)
        elif selected_dashboard_template_mode in {"auto", "none"}:
            template_mode, template_id = selected_dashboard_template_mode, None
        if template_mode != "manual":
            template_id = None
        target_title = str(args.get("title") or "").strip()
        if not target_title and invocation:
            target_title = str((invocation.context_snapshot or {}).get("dashboard_business_title") or "")
        if not target_title:
            from ...modules.reports.generation_quality import extract_requirement_title
            from ...modules.reports.query_delivery import conversion_requested
            # Grounded requirements include internal evidence labels. Use the
            # actual user request for names; generic conversion gets a clear default.
            if not conversion_requested(str(original)):
                target_title = extract_requirement_title(str(original or requirement))
        if args.get("query_ids") is not None or ctx.get("execution_owner") == "dsh" or (
                invocation and (invocation.context_snapshot or {}).get("chat_engine") == "dsh"):
            from ...modules.chat_artifacts.query_dashboard import create_dashboard_from_queries, current_query_ids
            query_ids = args.get("query_ids")
            if query_ids is None:
                query_ids = current_query_ids(auth_ctx, invocation) if invocation else []
            if not query_ids or len(query_ids) > 8:
                return ToolResult(status="failed", error_code="dashboard_query_selection_required",
                    error_summary="请先用 schema/sql 完成取数，再传入 1 至 8 个需要展示的 query_ids；尚未开始生成。",
                    data={"available_query_ids": query_ids},
                    metadata={"recovery": "correct_input", "execution_started": False, "business_data_queried": False})
            snapshot_context = {**ctx, "selected_datasource_ids": requested_ids,
                                "selected_dashboard_template_mode": template_mode,
                                "selected_dashboard_template_id": template_id}
            return create_dashboard_from_queries(auth_ctx, snapshot_context,
                {"query_ids": query_ids, "title": target_title or "业务查询结果大屏", "template_id": template_id,
                 "bindings": args.get("bindings")})
        payload = DashboardDraftInput(
            requestKey=request_key,
            requirement=requirement,
            title=target_title,
            datasourceIds=requested_ids,
            templateMode=template_mode,
            templateId=template_id,
            width=int(args.get("width") or 1920), height=int(args.get("height") or 1080),
        )
        from ...errors import AppError
        try:
            view = create_dashboard_draft(auth_ctx, payload, invocation_run_id=str(ctx.get("run_id") or "") or None)
        except AppError as error:
            from ...modules.business_ai.dashboard_actions import dashboard_failure_metadata
            return ToolResult(status="failed", error_code=error.code, error_summary=error.message,
                              metadata=dashboard_failure_metadata(error))
    return normalize({"status": "success", "data": view.model_dump(mode="json")}, tool_name=name)


def invoke(spec: ToolSpec, args: dict, ctx: dict) -> ToolResult:
    """内置工具统一执行入口，按 spec.name 分派到各实现分支。

    参数：``args`` 已经过 validator 校验（额外字段在此不再校验，依赖 schema 收紧）；
    ``ctx`` 需含 db/workspace_id/user_id/run_id 四项与 auth_context（网关已注入）。
    返回：ToolResult（多数为 success + data；带工件的工具会填充 artifacts 列表）。
    异常：未知工具名抛 ToolExecutionError；缺少 run_id 上下文、渲染失败、
    页数不一致等业务失败也抛 ToolExecutionError（由网关转成失败结果回给模型）。
    副作用：写文件的工具会落库并 commit；渲染类工具写入 artifact 表；
    查询类工具只读。
    """
    name = spec.name
    requested_tool_name = name
    if name == "query_inventory_data":
        from ...errors import ValidationError
        from ...modules.business_ai.context import auth_context_from_tool
        from ...modules.analytics.inventory import query_inventory
        from ...modules.analytics.schemas import InventoryQueryInput
        actor = ctx.get("auth_context") or auth_context_from_tool(ctx)
        selected = list(dict.fromkeys(ctx.get("selected_datasource_ids") or []))
        source_id = args.get("datasource_id") or (selected[0] if len(selected) == 1 else None)
        if not source_id or (selected and source_id not in selected):
            raise ValidationError("请选择本次明确授权的库存数据源；多选时指定 datasource_id")
        result = query_inventory(actor, InventoryQueryInput(datasourceId=source_id, kind=args.get("kind", "inventory"),
            warehouseId=args.get("warehouse_id", ""), materialId=args.get("material_id", ""), name=args.get("name", ""),
            batchNumber=args.get("batch_number", ""), offset=args.get("offset", 0), pageSize=args.get("page_size", 50)))
        return normalize({"status": "success", "data": result.model_dump(mode="json")}, tool_name=name)
    if name in {"render_chart_image", "render_dashboard_image", "create_dashboard_from_queries", "export_query_spreadsheet", "export_query_presentation"}:
        from ...modules.business_ai.context import auth_context_from_tool
        auth_ctx = ctx.get("auth_context") or auth_context_from_tool(ctx)
        if name == "export_query_spreadsheet":
            from ...modules.chat_artifacts.query_spreadsheet import export_query_spreadsheet
            return export_query_spreadsheet(auth_ctx, ctx, args)
        if name == "export_query_presentation":
            from ...modules.chat_artifacts.query_presentation import export_query_presentation
            return export_query_presentation(auth_ctx, ctx, args)
        if name == "render_chart_image":
            from ...modules.chat_artifacts.chart_renderer import render_chart_image
            return render_chart_image(auth_ctx, ctx, args)
        if name == "create_dashboard_from_queries":
            from ...modules.chat_artifacts.query_dashboard import create_dashboard_from_queries
            return create_dashboard_from_queries(auth_ctx, ctx, args)
        from ...modules.chat_artifacts.dashboard_image import render_dashboard_image
        return render_dashboard_image(auth_ctx, ctx, args)
    if name in {"list_business_skills", "read_business_skill_resource"}:
        from ...modules.octop_bridge.skills import list_skills, read_resource
        from ...errors import ValidationError
        try:
            data = ([{key: value for key, value in item.items() if key != "content"} for item in list_skills()]
                    if name == "list_business_skills" else read_resource(args["skill_name"], args["path"]))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return ToolResult(status="success", data=data)

    if name == 'export_inspection_report':
        from ...modules.business_ai.context import auth_context_from_tool
        from ...modules.alerts.inspection_export import export_report
        auth = ctx.get('auth_context') or auth_context_from_tool(ctx)
        view = export_report(auth, ctx.get('run_id'), args)
        result = normalize({'status':'success','data':view}, tool_name=name)
        result.artifacts = view['artifacts']
        return result
    if name == 'query_workflow_results':
        from ...modules.business_ai.context import auth_context_from_tool
        from ...modules.workflows.business_results import tool_result
        return normalize({'status': 'success', 'data': tool_result(ctx.get('auth_context') or auth_context_from_tool(ctx), args)}, tool_name=name)
    if name in {"list_dashboard_templates", "get_business_dashboard", "update_business_dashboard"}:
        from ...modules.business_ai.context import auth_context_from_tool
        from ...modules.business_ai.dashboard_actions import execute
        auth_ctx = ctx.get("auth_context") or auth_context_from_tool(ctx)
        return normalize({"status": "success", "data": execute(auth_ctx, name, args, ctx)}, tool_name=name)
    if name == "collect_device_monitoring_snapshot":
        from ...modules.business_ai.context import auth_context_from_tool
        from ...modules.alerts.monitoring import collect_snapshot
        auth_ctx = ctx.get("auth_context") or auth_context_from_tool(ctx)
        from sqlalchemy import select
        from ...modules.workflows.models import WorkflowExecution
        execution = auth_ctx.db.scalar(select(WorkflowExecution).where(
            WorkflowExecution.run_id == ctx.get('run_id'), WorkflowExecution.workspace_id == auth_ctx.workspace_id)) if ctx.get('run_id') else None
        window = (execution.result_json or {}).get('collectionWindow') if execution else None
        return normalize({"status": "success", "data": collect_snapshot(
            auth_ctx, args["datasource_id"], args["input"], args.get("category", "all"), window=window,
        )}, tool_name=name)
    if name in {"list_business_data_sources", "query_business_data", "view_device_alerts", "create_business_dashboard"}:
        return _business_action(name, args, ctx)
    if name == "current_datetime":
        now = datetime.now(timezone.utc).astimezone()
        return normalize({"status": "success",
                          "data": {"iso": now.isoformat(), "date": now.strftime("%Y-%m-%d"),
                                   "time": now.strftime("%H:%M:%S"), "timezone_offset": now.strftime("%z"),
                                   "utc_iso": now.astimezone(timezone.utc).isoformat()}}, tool_name=name)
    if name == "search_knowledge":
        return _search_knowledge(args, ctx)
    if name == "read_file":
        return _read_file(args, ctx)
    if name in ("list_files", "write_file", "copy_file", "rename_file", "delete_file"):
        return _file_ops(name, args, ctx)
    if name == "add_tool":
        return _add_tool(args, ctx)
    if name == "fill_template":
        return _fill_template(args, ctx)
    if name in {"create_presentation_plan", "write_presentation_slides", "finalize_presentation"}:
        from ...models import Run
        from ...modules.presentation.draft import (
            create_draft,
            final_spec,
            write_draft_slide_batches,
        )
        from ...modules.presentation.service import resolve_template

        keys = _ctx_keys(ctx)
        db = keys["db"]
        run_id = keys["run_id"]
        if not run_id:
            raise ToolExecutionError("缺少 run_id 上下文，无法保存 PPT 草稿")
        run = db.get(Run, run_id)
        snapshot = (run.context_snapshot or {}) if run else {}
        inputs = snapshot.get("plugin_inputs") or {}
        if name == "create_presentation_plan":
            selected_template = (
                snapshot.get("template_id") or inputs.get("template_id")
                or args.get("template_id") or "executive-ivory"
            )
            resolved = resolve_template(db, keys["workspace_id"], selected_template)
            status = create_draft(
                keys["workspace_id"], run_id, args,
                template_id=resolved["id"],
                aspect_ratio=inputs.get("aspect_ratio") or args.get("aspect_ratio") or "16:9",
                selected_page_count=inputs.get("page_count") or args.get("page_count"),
                user_request=str(snapshot.get("user_request") or snapshot.get("submitted_content") or ""),
            )
            return normalize({"status": "success", "data": status}, tool_name=name)
        if name == "write_presentation_slides":
            from ...modules.presentation.spec_normalizer import parse_json_array_partial
            batch = args.get("slides") or []
            if isinstance(batch, str):
                try:
                    batch = parse_json_array_partial(batch)
                except Exception as exc:
                    raise ToolExecutionError(f"本批页面 JSON 无法解析：{exc}")
            status = write_draft_slide_batches(
                keys["workspace_id"], run_id, batch, draft_id=args.get("draft_id")
            )
            return normalize({"status": "success", "data": status}, tool_name=name)
        staged_spec = final_spec(keys["workspace_id"], run_id, draft_id=args.get("draft_id"))
        args = {
            "presentation_spec": staged_spec,
            "template_id": staged_spec.get("template_id"),
            "aspect_ratio": staged_spec.get("aspect_ratio"),
            "page_count": staged_spec.get("page_count"),
            "name": args.get("name") or staged_spec.get("title") or "演示文稿",
        }
        name = "render_presentation"
    if name in ("render_document", "render_presentation", "render_spreadsheet", "save_json_artifact"):
        from ...modules.artifact.service import create_artifact
        from ...modules.artifact.renderers import RENDERERS
        keys = _ctx_keys(ctx)
        db = keys["db"]
        run_id = keys["run_id"]
        if not run_id:
            raise ToolExecutionError("缺少 run_id 上下文，无法创建工件")
        renderer = {
            "render_document": "document", "render_presentation": "presentation",
            "render_spreadsheet": "spreadsheet", "save_json_artifact": "json",
        }[name]
        ext = "docx"
        if name == "render_document":
            fmt = (args.get("format") or "docx").lower()
            renderer = "pdf" if fmt == "pdf" else "document"
            ext = "pdf" if fmt == "pdf" else "docx"
        elif name == "render_presentation":
            ext = "pptx"
        elif name == "render_spreadsheet":
            ext = "xlsx"
        elif name == "save_json_artifact":
            ext = "json"
        spec_in = args.get("document_spec") or args.get("presentation_spec") or args.get("spec") or args.get("data")
        resolved_template = None
        requested_page_count = None
        actual_page_count = None
        if name == "render_presentation":
            from ...modules.presentation.service import resolve_template
            from ...modules.presentation.spec_normalizer import normalize_presentation_spec
            from ...models import Run
            run = db.get(Run, run_id)
            snapshot = (run.context_snapshot or {}) if run else {}
            inputs = snapshot.get("plugin_inputs") or {}
            raw_object = spec_in if isinstance(spec_in, dict) else {}
            template_id = (
                snapshot.get("template_id") or inputs.get("template_id")
                or args.get("template_id") or raw_object.get("template_id")
                or "executive-ivory"
            )
            aspect_ratio = (
                inputs.get("aspect_ratio") or args.get("aspect_ratio")
                or raw_object.get("aspect_ratio") or "16:9"
            )
            selected_value = inputs.get("page_count")
            if selected_value in (None, "", "auto"):
                selected_value = None
            raw_page_count = selected_value or args.get("page_count") or raw_object.get("page_count")
            try:
                spec_in = normalize_presentation_spec(
                    spec_in,
                    title_fallback=str(args.get("name") or ""),
                    template_id=template_id,
                    aspect_ratio=aspect_ratio,
                    page_count=raw_page_count,
                )
            except Exception as exc:
                raise ToolExecutionError(f"PPT 结构解析失败：{exc}")
            if not spec_in.get("slides"):
                raise ToolExecutionError("PPT 没有可生成的正文页")
            resolved_template = resolve_template(db, keys["workspace_id"], template_id)
            spec_in["template_id"] = resolved_template["id"]
            if raw_page_count not in (None, "", "auto"):
                try:
                    requested_page_count = int(raw_page_count)
                except (TypeError, ValueError):
                    raise ToolExecutionError("PPT 总页数必须是 3–50 的整数")
                if requested_page_count < 3 or requested_page_count > 50:
                    raise ToolExecutionError("PPT 总页数必须在 3–50 页之间")
                spec_in["page_count"] = requested_page_count
            if resolved_template.get("template_path"):
                spec_in["_template_path"] = resolved_template["template_path"]
            quality_issues = _presentation_quality_issues(spec_in)
            if quality_issues:
                raise ToolExecutionError(
                    "PPT 内容不足，请补充后重新生成：" + "；".join(quality_issues[:6])
                )
        if name == "render_document":
            from ...modules.artifact.document_images import resolve_document_images
            spec_in = resolve_document_images(spec_in, db, keys["workspace_id"])
        try:
            fn = RENDERERS[renderer]
            data = fn(spec_in)
            if name == "render_presentation":
                import io
                from pptx import Presentation
                actual_page_count = len(Presentation(io.BytesIO(data)).slides)
                if requested_page_count and actual_page_count != requested_page_count:
                    raise ValueError(
                        f"指定 {requested_page_count} 页，但实际生成 {actual_page_count} 页"
                    )
        except Exception as exc:
            raise ToolExecutionError(f"渲染失败: {exc}")
        fallback = {"document": "文档", "presentation": "演示", "spreadsheet": "表格",
                    "pdf": "文档", "json": "数据"}[renderer]
        artifact_name = str(args.get("name") or fallback).strip()
        if not artifact_name.lower().endswith(f".{ext}"):
            artifact_name += f".{ext}"
        presentation_overflow = []
        if name == "render_presentation":
            from ...modules.presentation.engine import presentation_overflow_report
            presentation_overflow = presentation_overflow_report(spec_in)
        artifact = create_artifact(
            db, workspace_id=keys["workspace_id"], run_id=run_id, owner_user_id=keys["user_id"],
            type="pdf" if renderer == "pdf" else renderer,
            name=artifact_name, data=data,
            metadata={"tool": requested_tool_name,
                      "template_id": (resolved_template or {}).get("id") or args.get("template_id"),
                      "template_source": (resolved_template or {}).get("source"),
                      "requested_page_count": requested_page_count,
                      "actual_page_count": actual_page_count,
                      "presentation_overflow": presentation_overflow,
                      "presentation_schema": "presentation_spec.v2" if name == "render_presentation" else None,
                      "layout_roles": [
                          str(slide.get("layout") or slide.get("page_role") or "auto")
                          for slide in (spec_in.get("slides") or [])
                      ] if name == "render_presentation" and isinstance(spec_in, dict) else [],
                      "template_profile": (resolved_template or {}).get("analysis") or {},
                      "spec_summary": str(spec_in)[:500]})
        db.commit()
        return normalize({"status": "success",
                          "data": {"artifact_id": artifact.id, "name": artifact.name,
                                   "download_url": f"/api/artifacts/{artifact.id}/download",
                                   **({"presentation_overflow": presentation_overflow} if presentation_overflow else {})}},
                         tool_name=requested_tool_name)
    raise ToolExecutionError(f"未知的内置工具: {name}")


def _search_knowledge(args: dict, ctx: dict) -> ToolResult:
    """按当前知识授权检索片段，并在结束后恢复调用方的数据范围。

    与 HTTP 知识检索使用相同的 knowledge.view 作用域；共享库引用的源文件仅在
    本次检索期间可用于正文与来源复核，不因此授予后续文件读取、下载或附件权限。
    检索仍保留 File 联表和当前状态校验；未指定库时只检索当前身份可见的库。
    """
    from ...common.authorization import require_permission
    from ...common.data_scope import bind_data_scope
    from ...common.deps import AuthContext
    from ...errors import AuthError
    from ...modules.business_ai.context import auth_context_from_tool
    from ...modules.knowledge.service import search

    keys = _ctx_keys(ctx)
    db = keys["db"]
    scope_keys = ("resource_access_criteria", "actor_user_id", "actor_workspace_id")
    # 身份补建本身也会绑定作用域，因此必须在它之前保存，且保留字段原本不存在的语义。
    previous_scope = {key: db.info[key] for key in scope_keys if key in db.info}
    try:
        actor = ctx.get("auth_context")
        if actor is None:
            actor = auth_context_from_tool(ctx)
        elif (not isinstance(actor, AuthContext) or actor.db is not db
              or actor.user_id != keys["user_id"] or actor.workspace_id != keys["workspace_id"]):
            raise AuthError("知识检索授权上下文与当前执行身份不一致")
        require_permission(actor, "knowledge.view")
        bind_data_scope(actor, "knowledge.view")
        hits = search(db, workspace_id=keys["workspace_id"], query=args.get("query", ""),
                      knowledge_base_ids=args.get("knowledge_base_ids"),
                      top_k=int(args.get("top_k") or 5))
        return normalize({"status": "success", "data": {"results": hits}}, tool_name="search_knowledge")
    finally:
        # 只恢复授权三字段，不清空 Session.info 中检索诊断或其它调用方状态；嵌套调用同样安全。
        for key in scope_keys:
            if key in previous_scope:
                db.info[key] = previous_scope[key]
            else:
                db.info.pop(key, None)


def _read_file(args: dict, ctx: dict) -> ToolResult:
    """读取用户上传文件的文本内容，支持分页与关键词窗口两种模式。

    参数：``file_id`` 必须在当前工作区（get_file 会做归属校验）；
    ``query`` 存在时走关键词窗口模式（返回命中附近的片段，offset 恒为 0）；
    否则走分页模式，``offset`` / ``max_chars`` 决定本次窗口，返回值带
    next_offset 与 truncated 供模型续读；``include_images``/``max_images``
    控制是否调用视觉模型识别文中图片（最多 4 张，硬上限 8）。
    长度上限取 settings.READ_FILE_MAX_CHARS，下限 1000 字符。
    返回：文本、字符统计、分页信息、图片识别计数与告警列表。
    异常：文件不存在或不属于该工作区由 get_file 抛出；本函数不写库。
    """
    from ...modules.files.service import get_file
    from ...modules.files.extractor import extract_text
    from ...common.storage import get_storage
    keys = _ctx_keys(ctx)
    db = keys["db"]
    file_id = args.get("file_id") or ""
    file_row = get_file(db, keys["workspace_id"], file_id)
    storage = get_storage()
    path = storage.get_path(file_row.storage_key)
    blocks = extract_text(file_row.name, file_row.mime_type, path)
    text = "\n\n".join(b["text"] for b in blocks)
    vision_text = ""
    visual_image_count = 0
    vision_warnings: list[str] = []
    if args.get("include_images", True):
        requested_images = args.get("max_images")
        max_images = max(
            0,
            min(4 if requested_images is None else int(requested_images), 8),
        )
        if max_images:
            vision_text, visual_image_count, vision_warnings = _read_file_images(
                file_row,
                path,
                keys["workspace_id"],
                max_images,
            )
            if vision_text:
                text = (text + "\n\n" if text else "") + vision_text
    total_chars = len(text)
    requested_max = int(args.get("max_chars") or settings.READ_FILE_DEFAULT_MAX_CHARS)
    max_chars = max(1000, min(requested_max, settings.READ_FILE_MAX_CHARS))
    query = str(args.get("query") or "").strip()
    if query:
        returned_text, match_count = _matching_file_windows(text, query, max_chars)
        offset = 0
        next_offset = None
        truncated = len(returned_text) >= max_chars
        mode = "query"
    else:
        offset = max(0, min(int(args.get("offset") or 0), total_chars))
        end = min(offset + max_chars, total_chars)
        returned_text = text[offset:end]
        next_offset = end if end < total_chars else None
        match_count = None
        truncated = next_offset is not None
        mode = "page"
    return normalize({"status": "success",
                      "data": {
                          "file_id": file_row.id,
                          "name": file_row.name,
                          "text": returned_text,
                          "char_count": total_chars,
                          "returned_chars": len(returned_text),
                          "offset": offset,
                          "next_offset": next_offset,
                          "truncated": truncated,
                          "mode": mode,
                          "query": query or None,
                          "match_count": match_count,
                          "visual_image_count": visual_image_count,
                          "vision_warnings": vision_warnings,
                          "continuation_hint": (
                              f"继续读取请设置 offset={next_offset}"
                              if next_offset is not None
                              else None
                          ),
                      }}, tool_name="read_file")


def _read_file_images(
    file_row,
    path: str,
    workspace_id: str,
    max_images: int,
) -> tuple[str, int, list[str]]:
    """抽取文件中的图片并逐张调用视觉模型识别，返回 (识别文本, 成功数, 告警列表)。

    缓存键包含文件哈希、视觉模型名与图片数：同一文件在同一模型下不重复识别。
    单张识别失败只记 warning 并追加告警，不影响其余图片与整体读取（部分成功可接受）；
    缓存容量 128，超限时淘汰最早插入项（不按访问时间，实现更简单且足够）。
    副作用：调用外部模型服务（产生计费）；不在本函数内写数据库。
    """
    from ...modules.files.vision import extract_visual_assets
    from ...providers.model import _profile_config, analyze_image

    try:
        model_name = _profile_config(
            "vision", workspace_id=workspace_id
        ).get("model", "vision")
    except Exception:
        model_name = "vision"
    cache_key = f"{file_row.sha256}:{model_name}:{max_images}"
    with _VISION_CACHE_LOCK:
        cached = _VISION_TEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    assets, warnings = extract_visual_assets(
        file_row.name,
        file_row.mime_type,
        path,
        max_images=max_images,
    )
    sections: list[str] = []
    recognized = 0
    for image_index, asset in enumerate(assets):
        location = asset.get("location") or {}
        place = []
        if location.get("slide") is not None:
            place.append(f"幻灯片 {int(location['slide']) + 1}")
        elif location.get("page") is not None:
            place.append(f"第 {int(location['page']) + 1} 页")
        if location.get("sheet"):
            place.append(f"工作表 {location['sheet']}")
        if location.get("cell"):
            place.append(f"单元格 {location['cell']}")
        context = f"文件：{file_row.name}"
        if place:
            context += "；位置：" + " · ".join(place)
        try:
            result = analyze_image(
                asset["data"],
                asset["mime_type"],
                context=context,
                workspace_id=workspace_id,
            )
        except Exception as exc:
            warnings.append(
                f"图片 {image_index + 1} 识别失败：{type(exc).__name__}: {str(exc)[:180]}"
            )
            logger.warning(
                "read_file vision failed workspace=%s file=%s image=%s: %s",
                workspace_id,
                file_row.id,
                image_index,
                exc,
            )
            continue
        label = " · ".join(place) if place else f"图片 {image_index + 1}"
        sections.append(f"[图片识别：{label}]\n{result['content']}")
        recognized += 1
    value = ("\n\n".join(sections), recognized, warnings)
    with _VISION_CACHE_LOCK:
        if len(_VISION_TEXT_CACHE) >= 128:
            _VISION_TEXT_CACHE.pop(next(iter(_VISION_TEXT_CACHE)))
        _VISION_TEXT_CACHE[cache_key] = value
    return value


def _matching_file_windows(text: str, query: str, max_chars: int) -> tuple[str, int]:
    """按关键词从长文本中截取相关窗口，返回 (拼接后的文本, 命中位置数)。

    命中最多取 80 处（防止高频词把返回体撑满），每处前后各取约 1200/2400 字符，
    相邻窗口（间隔 ≤200 字符）会合并；最终按 max_chars 截断。
    未命中时返回中文提示与 0，调用方据此区分"没读到"与"文件为空"。
    大小写不敏感：用 casefold 归一化后再定位，避免中英混排漏匹配。
    """
    lowered = text.casefold()
    needles = [query.casefold()]
    if " " in query:
        needles.extend(
            token.casefold() for token in query.split() if len(token.strip()) >= 2
        )
    positions: list[int] = []
    for needle in dict.fromkeys(needles):
        start = 0
        while needle and len(positions) < 80:
            hit = lowered.find(needle, start)
            if hit < 0:
                break
            positions.append(hit)
            start = hit + max(1, len(needle))
    positions = sorted(set(positions))
    windows: list[tuple[int, int]] = []
    for position in positions:
        start = max(0, position - 1200)
        end = min(len(text), position + 2400)
        if windows and start <= windows[-1][1] + 200:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    parts: list[str] = []
    used = 0
    for index, (start, end) in enumerate(windows):
        header = f"[匹配片段 {index + 1}，字符 {start}-{end}]\n"
        available = max_chars - used - len(header)
        if available <= 0:
            break
        fragment = text[start:end][:available]
        parts.append(header + fragment)
        used += len(header) + len(fragment) + 2
        if used >= max_chars:
            break
    if not parts:
        return f"未找到关键词“{query}”。", 0
    return "\n\n".join(parts)[:max_chars], len(positions)





def _file_ops(name: str, args: dict, ctx: dict) -> ToolResult:
    """工作区文件操作集合：list_files / write_file / copy_file / rename_file / delete_file。

    所有操作都以 workspace_id 为范围，用户只能看到与改动本工作区文件；
    文件名会经 files.service 的校验（不允许路径穿越），因此这里不接受任意路径。
    write_file 支持 base64 二进制；delete_file 是软删除（mark_deleted），
    不影响其它会话对文件的引用；每次写操作都会 db.commit()。
    异常：文件名/ID 缺失或不合法抛 ToolExecutionError；未知操作名同样抛错。
    """
    import base64 as _b64
    from ...modules.files import service as fs
    from ...common.storage import get_storage
    keys = _ctx_keys(ctx)
    db = keys["db"]
    workspace_id = keys["workspace_id"]
    owner = keys["user_id"] or "system"
    if name == "list_files":
        q = (args.get("query") or "").strip().lower()
        rows = fs.list_files(db, workspace_id)
        if q:
            rows = [r for r in rows if q in r.name.lower()]
        items = [{"file_id": r.id, "name": r.name, "mime_type": r.mime_type,
                  "size": r.size} for r in rows]
        return normalize({"status": "success", "data": {"files": items, "count": len(items)}},
                         tool_name=name)
    if name == "write_file":
        fname = str(args.get("file_name") or "").strip()
        if not fname:
            raise ToolExecutionError("file_name 不能为空")
        if args.get("content_base64"):
            data = _b64.b64decode(args.get("content_base64"))
        else:
            data = str(args.get("content") or "").encode("utf-8")
        mime = args.get("mime_type") or "application/octet-stream"
        row = fs.save_upload(db, workspace_id=workspace_id, owner_user_id=owner,
                             filename=fname, mime_type=mime, data=data)
        db.commit()
        return normalize({"status": "success",
                          "data": {"file_id": row.id, "name": row.name, "size": row.size,
                                   "download_url": f"/api/files/{row.id}/download"}},
                         tool_name=name)
    if name == "copy_file":
        src = fs.get_file(db, workspace_id, str(args.get("file_id") or ""))
        storage = get_storage()
        data = storage.read_bytes(src.storage_key)
        new_name = fs.validate_renamed_filename(src.name, str(args.get("new_name") or fs.copied_filename(src.name)))
        row = fs.save_upload(db, workspace_id=workspace_id, owner_user_id=owner,
                             filename=new_name, mime_type=src.mime_type, data=data)
        db.commit()
        return normalize({"status": "success",
                          "data": {"file_id": row.id, "name": row.name, "source": src.name}},
                         tool_name=name)
    if name == "rename_file":
        file_id = str(args.get("file_id") or "")
        new_name = str(args.get("new_name") or "").strip()
        if not new_name:
            raise ToolExecutionError("new_name 不能为空")
        src = fs.rename_file(db, workspace_id, file_id, new_name)
        db.commit()
        return normalize({"status": "success", "data": {"file_id": src.id, "name": src.name}},
                         tool_name=name)
    if name == "delete_file":
        file_id = str(args.get("file_id") or "")
        fs.mark_deleted(db, workspace_id, file_id)
        db.commit()
        return normalize({"status": "success", "data": {"deleted": True}}, tool_name=name)
    raise ToolExecutionError(f"未知的文件操作: {name}")


def _add_tool(args: dict, ctx: dict) -> ToolResult:
    """复用管理台严格契约原子注册 HTTP 工具；不授权私网，不丢弃 URL 固定参数。"""
    from pydantic import ValidationError as ContractValidationError
    from ...errors import ValidationError
    from ...modules.business_ai.context import auth_context_from_tool
    from ...modules.capabilities.schemas import ConnectorInput, ToolDefinition
    from ...modules.capabilities.service import create_and_publish_connector
    from ...modules.collection.transport import validate_url

    actor = ctx.get("auth_context") or auth_context_from_tool(ctx)
    parsed = validate_url(args["url"])
    path = parsed.path or "/"
    if parsed.query:
        # provider 会在固定查询串后追加调用参数，必须保存完整查询串而不能静默丢弃。
        path += "?" + parsed.query
    try:
        definition = ToolDefinition(
            name=args["name"], description=args["description"],
            input_schema=args.get("input_schema", {"type": "object", "properties": {}}),
        )
        payload = ConnectorInput(
            name="运行时工具-" + definition.name, provider="http",
            config={"base_url": f"{parsed.scheme}://{parsed.netloc}", "path": path,
                    "method": args.get("method", "POST"), "body_mapping": "json",
                    "auth": {"type": "none"}, "headers": {}, "allow_private": False},
            tools=[definition],
        )
    except ContractValidationError as exc:
        # 不回显可能包含凭据的原始参数；配置校验的细节由共用服务安全表达。
        raise ValidationError("工具名称、说明或参数 Schema 不符合连接器注册契约") from exc
    result = create_and_publish_connector(actor, payload)
    return normalize({"status": "success",
                      "data": {"tool_name": definition.name, "connector_id": result["id"],
                               "registered": True}}, tool_name="add_tool")


def _fill_template(args: dict, ctx: dict) -> ToolResult:
    """把数据填入用户上传的模板文件（.xlsx/.docx），保留模板原有格式。

    参数：``file_id`` 必填且必须属于当前工作区；``cells`` 按坐标填、
    ``by_label`` 按表头标签填、``tables`` 追加表格行、``replace`` 处理 docx 占位符、
    ``font`` 控制填入内容字体；``name`` 缺省为"原模板名-已填入.扩展名"。
    返回：artifact_id/name/download_url 与写入计数（written）。
    异常：不支持的扩展名、缺少 file_id、填充失败均抛 ToolExecutionError。
    副作用：先在 storage/artifacts 下生成中间文件，再创建 artifact 记录并 commit；
    中间文件不对外暴露，仅作渲染输入。
    """
    import os
    from ...modules.files.service import get_file
    from ...modules.files.template_fill import fill_template_file
    from ...modules.artifact.service import create_artifact
    from ...common.storage import get_storage
    keys = _ctx_keys(ctx)
    db = keys["db"]
    file_id = str(args.get("file_id") or "")
    if not file_id:
        raise ToolExecutionError("缺少 file_id（上传的模板文件）")
    row = get_file(db, keys["workspace_id"], file_id)
    ext = os.path.splitext(row.name)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".docx", ".doc"):
        raise ToolExecutionError(f"模板填充暂不支持 {ext}（支持 .xlsx / .docx）")
    storage = get_storage()
    src_path = storage.get_path(row.storage_key)
    out_name = str(args.get("name") or "").strip()
    if not out_name:
        base = os.path.splitext(row.name)[0]
        out_name = f"{base}-已填入{ext}"
    final_ext = os.path.splitext(out_name)[1].lower() or ext
    out_name = out_name if os.path.splitext(out_name)[1] else (out_name + ext)
    tmp_key = f"artifacts/{keys['workspace_id'][:8]}/fill_{file_id}_{os.path.basename(out_name)}"
    tmp_path = storage.get_path(tmp_key)
    try:
        meta = fill_template_file(
            src_path, tmp_path, ext=ext,
            cells=args.get("cells"), by_label=args.get("by_label"),
            tables=args.get("tables"), replace=args.get("replace"), font=args.get("font"),
        )
    except Exception as exc:
        raise ToolExecutionError(f"模板填充失败: {exc}")
    data = storage.read_bytes(tmp_key)
    artifact = create_artifact(
        db, workspace_id=keys["workspace_id"], run_id=keys["run_id"], owner_user_id=keys["user_id"] or "system",
        type="spreadsheet" if ext in (".xlsx", ".xlsm") else "document",
        name=out_name, data=data,
        metadata={"tool": "fill_template", "source_file_id": file_id, "fill": meta})
    db.commit()
    return normalize({"status": "success",
                      "data": {"artifact_id": artifact.id, "name": artifact.name,
                               "download_url": f"/api/artifacts/{artifact.id}/download",
                               "written": meta.get("written")}}, tool_name="fill_template")
