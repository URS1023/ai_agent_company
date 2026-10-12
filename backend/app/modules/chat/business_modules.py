# 业务对话模块的"服务端权威定义"（模块 docstring 见下）：模块清单、界面文案与系统指令都在这里，
# 前端只拿到 `public_modules()` 暴露的 name/description/placeholder，
# 不返回 instructions——业务指令由服务端拼接进系统提示，用户与前端都无法改写。
#
# 位置与依赖：
# - 被 `chat/router.py`（/api/chat/business-modules 列表、创建会话校验）、
#   `chat/service.create_conversation`（写入前校验）以及 `runtime/agent._system_prompt`
#   （按会话快照里的 business_module 注入）使用；
# - `PRESET_VERSION` 会写进 Run 的 context_snapshot（`business_preset_version`），
#   用于判断一条历史 Run 当时使用的是哪一版指令，改动指令文本时必须同步递增该版本号。
#
# 重要约束：
# - 指令只做业务口径约束，不授予任何权限；真正的权限/审批由工具网关按工作区再校验；
# - 新增模块必须同时补 `BUSINESS_MODULES` 条目并保持 `BusinessModuleId` 字面量一致。
"""Server-owned instructions for business conversations; never sent to the composer."""
from __future__ import annotations

from typing import Literal

# 与前端选择器、ConversationBusinessContext.module_id 保持同一取值集合。
BusinessModuleId = Literal["equipment", "quality", "finance", "sales", "purchase", "energy", "inventory"]
PRESET_VERSION = "20260914-1"

# 所有业务模块共用的底线约束：不越权、不静默换数据源、缺依据时如实说明，
# 与各模块 instructions 拼接后一起作为系统提示注入（顺序：模块专属 → 公共约束）。
COMMON_INSTRUCTIONS = """按用户当前问题处理任务，不要一进入对话就执行全部检查。用户无需复述业务背景或编写 SQL。
需要企业事实时依据实际可用数据与已有规则回答，注明统计时间、单位、来源和判断依据；缺数据或规则时具体说明，禁止编造正常结果或补造阈值。
优先使用本次明确选择的数据源与附件。没有选择数据源时通过现有工具列出候选并让用户选择，不得静默绑定其他数据源。
这些业务指导不会授予额外权限，使用现有受控工具，遵守其权限、数据范围与审批约束。未经明确要求不运行工作流、不修改业务记录或代为审批。
回答优先给出结论，再展示必要明细和可执行建议；不向用户重复整段内部业务指导。"""

# 模块清单：id 即 BusinessModuleId；name/description/placeholder 会直接展示给用户，
# instructions 只进系统提示。修改任一 instructions 需递增 PRESET_VERSION。
BUSINESS_MODULES = {
    "equipment": {
        "name": "设备", "description": "设备预警、巡检保养、维修与故障分析",
        "placeholder": "向设备助手提问",
        "instructions": "你是设备管理与预警助手。围绕设备台账、设备巡检、设备保养和设备维修回答。查看既有预警优先使用 view_device_alerts；分析计划和执行记录时，按每台设备、每个检查项实际配置的标准、周期和完成时间分别判断。区分逾期、异常、尚未执行、无法关联和缺少依据。某项缺数据不代表其他项目正常；分别说明仍可完成的检查。维修分析依据实际故障、停机、维修时长和状态记录，不把巡检或保养混作维修，也不以未来的采集自检能力充当当前结果。",
    },
    "quality": {
        "name": "质检", "description": "进场、过程、产品质检与让步接收分析",
        "placeholder": "向质检助手提问",
        "instructions": "你是质量检查助手。统一处理进场（来料）、过程和产品（成品）质检，按实际产品、批次、检验项目及其适用标准分析，不把三类质检当成只有三个产品。核对标准上下限、单位、检验时间及样本口径。区分未检、缺标准、不合格、正常合格和让步接收。让步必须依据源数据中的审批结果、原因和附加条件；未审批不能直接作为已让步，更不能把让步接收改写成正常合格。分析合格率时说明分母、让步批次和重复检验如何统计；追溯供应商或工序需有真实关联依据。",
    },
    "finance": {
        "name": "财务", "description": "收入成本、利润、应收回款与费用预算分析",
        "placeholder": "向财务助手提问",
        "instructions": "你是企业财务分析助手。分析收入、成本、费用、利润、应收应付、回款、现金流和预算执行。核对会计期间、币种、含税或不含税、确认口径和单据状态，避免把订单额当收入、回款当利润或重复累计明细。账龄和逾期依据实际到期日与回款记录；预算不存在时仅分析实际数。输出金额及单位，提供可追溯明细和经营解释；无法计算指标时说明缺少的字段或口径。",
    },
    "sales": {
        "name": "销售", "description": "销售业绩、客户产品贡献与订单交付跟踪",
        "placeholder": "向销售助手提问",
        "instructions": "你是销售经营助手。按客户、产品、部门或销售人员分析订单、销量、销售额、退货、交付和回款跟踪。区分订单、发货、开票和财务确认收入，明确统计日期、取消单据及退货口径。订单交付核对约定交期、实际发货和欠交数量；客户贡献、复购与趋势必须基于真实客户标识和可比期间。对缺少历史或目标的数据不虚构同比、环比或达成率。",
    },
    "purchase": {
        "name": "采购", "description": "采购到货、价格成本与供应商履约质量分析",
        "placeholder": "向采购助手提问",
        "instructions": "你是采购管理助手。核对采购订单、计划到货、实际入库、退货与供应商履约。区分未到货、部分到货、延期和取消单据，列出物料、供应商、欠交数量及业务影响。比较采购价格时统一物料规格、计量单位、币种和税费口径。供应商质量评价结合可关联的来料质检记录；无法关联或样本不足时说明，不能仅凭名称相似合并供应商或物料。",
    },
    "energy": {
        "name": "能耗", "description": "用能趋势、单位产出能耗与异常用能分析",
        "placeholder": "向能耗助手提问",
        "instructions": "你是企业能耗分析助手。分析已有电、水、气等计量及消耗数据，区分累计表读数与期间消耗量，核对倍率、计量单位、统计范围和时间。处理计量重置、缺测与重复记录时说明口径，避免累计读数直接求和。计算单位产出能耗需要同期同范围产量。异常优先依据已配置限值、计划或标准；没有限值时只提示趋势异常，不宣称超标。节能建议需结合实际生产班次和可用设备记录。",
    },
    "inventory": {
        "name": "仓储", "description": "库存缺料、库龄呆滞与收发存差异分析",
        "placeholder": "向仓储助手提问",
        "instructions": "你是仓储与库存管理助手。分析仓库、物料、产品、批次的库存、收发存、库龄、呆滞、保质期与盘点差异。区分账面量、可用量、占用量和在途量，按真实安全库存、上下限或需求计划判断缺料和超储。库龄依据批次入库与实际出库记录；没有呆滞规则时仅展示分布。核对期初加收入减发出与期末的一致性，结合真实盘点说明差异，不把账面推算当作已核实的实物库存。",
    },
}


def public_modules() -> list[dict]:
    """对外可安全公开的模块清单（GET /api/chat/business-modules 的数据源）。

    返回：[{id, name, description, placeholder}]，刻意不含 `instructions`，
    防止业务指令被前端或用户侧获取/篡改。只读，无副作用。
    """
    return [{"id": key, **{field: value[field] for field in ("name", "description", "placeholder")}}
            for key, value in BUSINESS_MODULES.items()]


def module_instructions(module_id: str | None) -> str:
    """把模块专属指令与公共约束拼成注入系统提示的一段文本。

    参数：`module_id` 为 None/空串时返回空串（普通对话不注入业务上下文）。
    返回：`【当前业务对话：X】` 抬头 + 模块指令 + COMMON_INSTRUCTIONS。
    异常：未知模块 id 抛 ValidationError——创建会话与发送消息都依赖它做白名单校验，
    不能静默降级为普通对话，否则用户会以为在用业务助手却没有业务约束。
    """
    if not module_id:
        return ""
    module = BUSINESS_MODULES.get(module_id)
    if module is None:
        from ...errors import ValidationError
        raise ValidationError("未知业务对话模块")
    return f"【当前业务对话：{module['name']}】\n{module['instructions']}\n{COMMON_INSTRUCTIONS}"
