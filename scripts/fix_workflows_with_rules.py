"""Fix all 15 fine-grained workflows: add real DB queries, deviation calculation,
threshold rule evaluation and AI diagnosis.

For each of the 15 already-deployed workflows (rev 2), this script:
  1. Fetches the current workflow (GET /api/v1/workflows/{id}) to read current version
  2. PUTs the corrected graph (database-query -> calculate-values -> rule-evaluate -> llm -> end)
  3. Publishes the new revision

Quality workflows (9) use a JOIN SQL query against:
  t_qualityinspectionitemdetails (measured values) + t_qualityinspectionproject (thresholds)

Equipment workflows (6) use a SQL query against:
  t_deviceinspectiondetails (xunjian records) + t_devicemanagement (device info)
with hard-coded threshold rules (since t_detailsofinspectionindicators has no numeric limits).
"""
import os
import json
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8011"
DATASOURCE_ID = "01M24QGNNN404ZF3A9XB48FRY5"  # szh_new local MySQL


class Client:
    def __init__(self):
        login_req = urllib.request.Request(
            f"{BASE_URL}/api/auth/login",
            # 登录密码由运行环境提供，避免将本机凭据提交到仓库。
            data=json.dumps({"username": "workspace-admin", "password": os.environ["WORKSPACE_ADMIN_PASSWORD"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(login_req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.token = data["token"]
            self.workspace_id = data["workspaces"][0]["id"]
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-Workspace-Id": self.workspace_id
        }

    def get(self, path):
        req = urllib.request.Request(f"{BASE_URL}{path}", headers=self.headers)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def put(self, path, payload):
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers,
            method="PUT"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            print(f"  HTTP Error {e.code} on PUT {path}: {body}")
            raise

    def post(self, path, payload):
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            print(f"  HTTP Error {e.code} on POST {path}: {body}")
            raise


# ---------------------------------------------------------------------------
# Quality workflow graph builder
# ---------------------------------------------------------------------------

def build_quality_graph(mat_name, mat_code, proj_type_code, indicators, rules, sys_prompt):
    """
    5-node graph:
      start -> database-query (JOIN actual + standard) ->
      calculate-values (deviations) -> rule-evaluate (threshold checks) ->
      llm (AI diagnosis) -> end
    """
    join_sql = (
        "SELECT"
        " d.c_testresults AS xice_value,"
        " p.c_projectName AS indicator_name,"
        " p.c_standardMin AS std_min,"
        " p.c_standardMax AS std_max,"
        " p.c_judgeRule AS judge_rule,"
        " p.c_shifoushizhuyaopanduanbiaozhun AS is_critical,"
        " f.c_incomingQcNo AS qc_no,"
        " f.c_samplingTime AS sample_time,"
        " f.c_testResults AS qc_result"
        " FROM t_incomingmaterialqcform f"
        " JOIN t_qualityinspectionitemdetails d"
        "   ON d.mdIncomingMaterialQcFormId = f.incomingMaterialQcFormId"
        " JOIN t_qualityinspectionproject p"
        "   ON p.qualityInspectionProjectId = d.c_qualityInspectionProject"
        f" WHERE f.isDeleted = 0 AND p.isDeleted = 0 AND p.c_projectType = {proj_type_code}"
        " ORDER BY f.autoId DESC LIMIT 30"
    )

    # Build deviation calculations from indicators list
    dev_calcs = []
    for ind in indicators:
        if ind.get("deviation_name"):
            dev_calcs.append({
                "name": ind["deviation_name"],
                "operation": "subtract",
                "left": {"source": "field", "field": "xice_value"},
                "right": {"source": "constant", "value": ind["standard_ref"]}
            })

    nodes = [
        {
            "id": "start",
            "kind": "start",
            "title": "开始",
            "position": {"x": 50, "y": 200},
            "config": {
                "variables": [
                    {
                        "variable": "batchNo",
                        "label": "批次号",
                        "type": "text-input",
                        "required": False,
                        "hint": f"留空时自动读取最新【{mat_name}】质检记录"
                    }
                ]
            }
        },
        {
            "id": "read_indicators",
            "kind": "database-query",
            "title": f"读取 · {mat_name}检验指标实测值与标准对比",
            "position": {"x": 320, "y": 200},
            "config": {
                "datasourceId": DATASOURCE_ID,
                "datasourceVersion": None,
                "query": join_sql,
                "parameters": {},
                "maxRows": 50
            }
        },
        {
            "id": "calc_deviations",
            "kind": "calculate-values",
            "title": f"计算 · {mat_name}各指标偏差量",
            "position": {"x": 680, "y": 200},
            "config": {
                "calculations": [
                    {
                        "name": "核查时间",
                        "operation": "now",
                        "left": {"source": "constant", "value": ""}
                    },
                    {
                        "name": "材料名称",
                        "operation": "copy",
                        "left": {"source": "constant", "value": mat_name}
                    },
                    {
                        "name": "材料编码",
                        "operation": "copy",
                        "left": {"source": "constant", "value": mat_code}
                    }
                ] + dev_calcs
            }
        },
        {
            "id": "rule_check",
            "kind": "rule-evaluate",
            "title": f"规则判断 · {mat_name}指标合规性评估",
            "position": {"x": 1040, "y": 200},
            "config": {
                "mode": "quality",
                "rules": rules
            }
        },
        {
            "id": "ai_diagnosis",
            "kind": "llm",
            "title": f"AI · {mat_name}品质智能诊断",
            "position": {"x": 1400, "y": 200},
            "config": {
                "modelProfile": "reasoning",
                "systemPrompt": sys_prompt,
                "promptTemplate": (
                    f"【检验对象】：{mat_name}（编码：{mat_code}）\n"
                    "【数据库实测与标准对比数据（含规则判断结果）】：\n{{ input }}\n\n"
                    "请严格依据上述实测指标与阈值判断规则进行专业评审，输出：\n"
                    "1. 【品质合规结论】（列出每项超标指标及偏差量，明确合格/不合格）；\n"
                    "2. 【工艺风险影响】（超标指标对电石冶炼炉况/产品质量的直接影响）；\n"
                    "3. 【现场处置指令】（明确给出：放行入库 / 降级扣款 / 隔离复检 / 立即退货拒收）。"
                ),
                "temperature": 0.1
            }
        },
        {
            "id": "end",
            "kind": "end",
            "title": "结束 · 输出报告",
            "position": {"x": 1760, "y": 200},
            "config": {}
        }
    ]
    edges = [
        {"id": "e1", "source": "start",           "target": "read_indicators", "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e2", "source": "read_indicators",  "target": "calc_deviations", "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e3", "source": "calc_deviations",  "target": "rule_check",      "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e4", "source": "rule_check",       "target": "ai_diagnosis",    "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e5", "source": "ai_diagnosis",     "target": "end",             "port": "out", "sourceSide": "right", "targetSide": "left"},
    ]
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Equipment workflow graph builder
# ---------------------------------------------------------------------------

def build_equipment_graph(dev_name, dev_code, dev_name_like, rules, sys_prompt, cycle_desc):
    """
    5-node graph:
      start -> database-query (recent inspection records for this device) ->
      calculate-values (timestamps/metadata) -> rule-evaluate (hard-coded limits) ->
      llm (AI diagnosis) -> end
    """
    insp_sql = (
        "SELECT"
        " ins.c_inspectionName AS inspection_item,"
        " ins.c_inspectionResult AS inspection_result,"
        " ins.c_inspectionIndicators AS indicator_value,"
        " dm.c_deviceName AS device_name,"
        " dm.c_deviceStatus AS device_status,"
        " dm.c_deviceBuilding AS install_area"
        " FROM t_deviceinspectiondetails ins"
        " JOIN t_devicemanagement dm ON dm.isDeleted = 0"
        f" WHERE ins.isDeleted = 0 AND dm.c_deviceName LIKE '%{dev_name_like}%'"
        " ORDER BY ins.autoId DESC LIMIT 20"
    )

    nodes = [
        {
            "id": "start",
            "kind": "start",
            "title": "开始",
            "position": {"x": 50, "y": 200},
            "config": {
                "variables": [
                    {
                        "variable": "deviceNo",
                        "label": "设备编号",
                        "type": "text-input",
                        "required": False,
                        "hint": f"默认监测【{dev_name}】"
                    }
                ]
            }
        },
        {
            "id": "read_inspections",
            "kind": "database-query",
            "title": f"读取 · {dev_name}巡检记录与运行状态",
            "position": {"x": 320, "y": 200},
            "config": {
                "datasourceId": DATASOURCE_ID,
                "datasourceVersion": None,
                "query": insp_sql,
                "parameters": {},
                "maxRows": 30
            }
        },
        {
            "id": "calc_runtime",
            "kind": "calculate-values",
            "title": f"计算 · {dev_name}运行状态核查",
            "position": {"x": 680, "y": 200},
            "config": {
                "calculations": [
                    {
                        "name": "核查时间",
                        "operation": "now",
                        "left": {"source": "constant", "value": ""}
                    },
                    {
                        "name": "监控设备名称",
                        "operation": "copy",
                        "left": {"source": "constant", "value": dev_name}
                    },
                    {
                        "name": "设备编码",
                        "operation": "copy",
                        "left": {"source": "constant", "value": dev_code}
                    },
                    {
                        "name": "监控周期说明",
                        "operation": "copy",
                        "left": {"source": "constant", "value": cycle_desc}
                    }
                ]
            }
        },
        {
            "id": "rule_check",
            "kind": "rule-evaluate",
            "title": f"规则判断 · {dev_name}健康状态评估",
            "position": {"x": 1040, "y": 200},
            "config": {
                "mode": "alert",
                "rules": rules
            }
        },
        {
            "id": "ai_diagnosis",
            "kind": "llm",
            "title": f"AI · {dev_name}健康诊断与预警",
            "position": {"x": 1400, "y": 200},
            "config": {
                "modelProfile": "reasoning",
                "systemPrompt": sys_prompt,
                "promptTemplate": (
                    f"【受监设备】：{dev_name}（编号：{dev_code}）\n"
                    f"【监控周期与维保标准】：{cycle_desc}\n"
                    "【最新巡检记录与规则判断结果如下】：\n{{ input }}\n\n"
                    "请给出专业设备工程诊断：\n"
                    "1. 【设备隐患与健康定级】（是否超温、超压、超期未润滑或存在非计划停机风险）；\n"
                    "2. 【故障机理与诱因推断】（如绝缘劣化、轴承干磨、油压泄漏等）；\n"
                    "3. 【现场检修与维保处置建议】（巡检重点点位、润滑规格、抢修优先级）。"
                ),
                "temperature": 0.1
            }
        },
        {
            "id": "end",
            "kind": "end",
            "title": "结束 · 报告输出",
            "position": {"x": 1760, "y": 200},
            "config": {}
        }
    ]
    edges = [
        {"id": "e1", "source": "start",           "target": "read_inspections", "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e2", "source": "read_inspections", "target": "calc_runtime",    "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e3", "source": "calc_runtime",     "target": "rule_check",      "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e4", "source": "rule_check",       "target": "ai_diagnosis",    "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e5", "source": "ai_diagnosis",     "target": "end",             "port": "out", "sourceSide": "right", "targetSide": "left"},
    ]
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Workflow definitions (ID => corrected spec)
# ---------------------------------------------------------------------------

QUALITY_WORKFLOWS = [
    {
        "id": "01M2N839WPPP2EKAH0TG99V4TC",
        "name": "焦炭进厂质检工作流",
        "description": "按车次/批次检测入炉核心碳素原料（焦炭 01202511280002），14项化验指标比对，灰分/挥发分主要项一票否决，AI智能诊断与入库指导。",
        "domains": ["quality"],
        "mat_name": "焦炭",
        "mat_code": "01202511280002",
        "proj_type_code": 2,
        "sys_prompt": "你是电石冶炼焦炭原料质量资深专家。严格对照GB/T 1996-2017工业焦炭标准，核查关键指标，识别灰分超标导致的电炉比电阻下降风险，给出拒收或放行指令。",
        "indicators": [
            {"deviation_name": "固定碳偏差", "standard_ref": 82.0},
            {"deviation_name": "灰分偏差",   "standard_ref": 12.0},
            {"deviation_name": "水分偏差",   "standard_ref": 5.0},
        ],
        "rules": [
            {
                "id": "coke_fixed_carbon",
                "name": "固定碳不低于82%",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 82.0}]},
                "severity": "critical",
                "message": "焦炭固定碳实测值低于82%，导致炉内还原能力不足，炉况恶化"
            },
            {
                "id": "coke_ash",
                "name": "灰分不高于12%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 12.0}]},
                "severity": "critical",
                "message": "焦炭灰分超过12%，降低炉料导电性，增加电石渣量"
            },
            {
                "id": "coke_volatile",
                "name": "挥发分不高于1.9%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 1.9}]},
                "severity": "warning",
                "message": "焦炭挥发分偏高，影响电极烧结与炉内气氛"
            },
            {
                "id": "coke_moisture",
                "name": "水分不高于5%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 5.0}]},
                "severity": "warning",
                "message": "焦炭水分超标，增加热耗并影响电极把持稳定性"
            }
        ]
    },
    {
        "id": "01M2N83A4M6NV0RWT75N3CZTHG",
        "name": "石灰石进厂质检工作流",
        "description": "针对煅烧石灰石原料（01202511280003），比对CaO品位、酸不溶物、粒度等指标，AI诊断煅烧活性度预警。",
        "domains": ["quality"],
        "mat_name": "石灰石",
        "mat_code": "01202511280003",
        "proj_type_code": 3,
        "sys_prompt": "你是石灰窑煅烧原料专家。审核石灰石纯度与粒度，防止低品位石灰石导致窑内结瘤或烧制生灰有效钙不足。",
        "indicators": [
            {"deviation_name": "CaO偏差", "standard_ref": 53.0},
        ],
        "rules": [
            {
                "id": "limestone_cao",
                "name": "CaO含量不低于53%",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 53.0}]},
                "severity": "critical",
                "message": "石灰石CaO含量低于53%，煅烧后有效钙不足，影响电石发气量"
            },
            {
                "id": "limestone_mgo",
                "name": "MgO含量不高于2.5%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 2.5}]},
                "severity": "warning",
                "message": "石灰石MgO超标，煅烧炉结瘤风险增加"
            },
            {
                "id": "limestone_sio2",
                "name": "SiO2不高于1.5%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 1.5}]},
                "severity": "warning",
                "message": "SiO2杂质超标，增加电石冶炼渣量，降低收率"
            }
        ]
    },
    {
        "id": "01M2N83AB3ZYTG150GFE40E8KZ",
        "name": "兰炭进厂质检工作流",
        "description": "检测新型碳素还原剂兰炭（CP202604010391），7项指标比对，评价高发热量与固定碳，AI诊断配料比调整方案。",
        "domains": ["quality"],
        "mat_name": "兰炭",
        "mat_code": "CP202604010391",
        "proj_type_code": 9,
        "sys_prompt": "你是电石炉还原剂工艺专家。评估兰炭比电阻和反应活性，依据检测数据指导炉前配料比与电耗优化。",
        "indicators": [
            {"deviation_name": "固定碳偏差", "standard_ref": 80.0},
            {"deviation_name": "灰分偏差",   "standard_ref": 9.0},
        ],
        "rules": [
            {
                "id": "semi_coke_fc",
                "name": "兰炭固定碳不低于80%",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 80.0}]},
                "severity": "critical",
                "message": "兰炭固定碳低于80%，还原效率不足，电石收率下降"
            },
            {
                "id": "semi_coke_ash",
                "name": "兰炭灰分不高于9%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 9.0}]},
                "severity": "warning",
                "message": "兰炭灰分超标，影响炉内导电性和电石纯度"
            },
            {
                "id": "semi_coke_sulfur",
                "name": "全硫不高于0.4%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 0.4}]},
                "severity": "warning",
                "message": "兰炭全硫超标，生成硫化氢影响电石产品杂质气体指标"
            }
        ]
    },
    {
        "id": "01M2N83AHQBV8CG6BGN7JEF4D0",
        "name": "生石灰进厂质检工作流",
        "description": "检测入炉主要钙质原料生石灰（01202511280001），16项指标比对，有效CaO、MgO、生过烧率一票否决，AI快速诊断。",
        "domains": ["quality"],
        "mat_name": "生石灰",
        "mat_code": "01202511280001",
        "proj_type_code": 1,
        "sys_prompt": "你是冶炼生石灰质量评审专家。生石灰有效钙不足或生烧过高会直接造成电石发气量暴跌并引发炉膛翻渣翻料，严格执行一票否决。",
        "indicators": [
            {"deviation_name": "有效CaO偏差", "standard_ref": 88.0},
        ],
        "rules": [
            {
                "id": "lime_cao",
                "name": "有效CaO不低于88%",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 88.0}]},
                "severity": "critical",
                "message": "生石灰有效CaO低于88%，电石发气量将大幅下降，炉况不稳"
            },
            {
                "id": "lime_mgo",
                "name": "MgO不高于2.0%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 2.0}]},
                "severity": "warning",
                "message": "生石灰MgO偏高，影响石灰活性与炉内反应速率"
            },
            {
                "id": "lime_overburn",
                "name": "过烧率不高于4%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 4.0}]},
                "severity": "warning",
                "message": "生石灰过烧率偏高，遇水消化速率过低，影响炉内反应动力学"
            },
            {
                "id": "lime_underburn",
                "name": "生烧率不高于6%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 6.0}]},
                "severity": "warning",
                "message": "生石灰生烧率偏高，炉内带入CO2影响电石收率"
            }
        ]
    },
    {
        "id": "01M2N83AR4XSCZYX6GKGXX3DT1",
        "name": "电极糊进厂质检工作流",
        "description": "针对自焙电极关键辅料电极糊（01202511280004），11项理化指标比对，防止断电极与软断事故，AI评估烧结特性。",
        "domains": ["quality"],
        "mat_name": "电极糊",
        "mat_code": "01202511280004",
        "proj_type_code": 4,
        "sys_prompt": "你是电石炉自焙电极专业工程师。电极糊挥发分偏差会导致电极漏糊或硬断，根据实测指标输出烧结成熟度预测与加糊指令。",
        "indicators": [
            {"deviation_name": "挥发分偏差", "standard_ref": 12.0},
        ],
        "rules": [
            {
                "id": "ep_volatile_min",
                "name": "挥发分不低于10.5%",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 10.5}]},
                "severity": "critical",
                "message": "电极糊挥发分低于10.5%，烧结过快导致电极硬断"
            },
            {
                "id": "ep_volatile_max",
                "name": "挥发分不高于13.5%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 13.5}]},
                "severity": "critical",
                "message": "电极糊挥发分超过13.5%，导致糊柱软断漏糊"
            },
            {
                "id": "ep_ash",
                "name": "灰分不高于4%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 4.0}]},
                "severity": "warning",
                "message": "电极糊灰分超标，电极导电性下降，电阻发热异常"
            }
        ]
    },
    {
        "id": "01M2N83AYF221MR3QT59V5TN7M",
        "name": "炉前电石快速质检工作流",
        "description": "监控电石出炉前后采样快速化验，发气量、硫化氢、磷化氢3项关键指标比对，AI推断炉膛反应完全度与炉况。",
        "domains": ["quality"],
        "mat_name": "炉前电石",
        "mat_code": "06202511290005",
        "proj_type_code": 7,
        "sys_prompt": "你是电石炉炉前操作总工程师。根据发气量快速判定三相反应深度，指导炉前送电功率、电极下插深度与排料节奏。",
        "indicators": [
            {"deviation_name": "发气量偏差", "standard_ref": 280.0},
        ],
        "rules": [
            {
                "id": "furnace_carbide_gas",
                "name": "出炉发气量不低于280L/kg",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 280.0}]},
                "severity": "critical",
                "message": "炉前电石发气量低于280L/kg，炉内反应不完全，需调整送电参数"
            },
            {
                "id": "furnace_carbide_h2s",
                "name": "硫化氢不高于0.12%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 0.12}]},
                "severity": "warning",
                "message": "硫化氢超标，原料含硫偏高或炉内温度分布异常"
            },
            {
                "id": "furnace_carbide_ph3",
                "name": "磷化氢不高于0.06%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 0.06}]},
                "severity": "warning",
                "message": "磷化氢超标，电石产品杂质气体超国标，需调查磷来源"
            }
        ]
    },
    {
        "id": "01M2N83B58T770B12YS09J5NC4",
        "name": "成品电石品质分级质检工作流",
        "description": "成品电石包装出厂前全面质检，发气量国标分级（优等品>=305L/kg、一级品>=280L/kg），AI生成出厂合格证明与分级报告。",
        "domains": ["quality"],
        "mat_name": "成品电石",
        "mat_code": "06202511290005",
        "proj_type_code": 11,
        "sys_prompt": "你是成品出厂品质总监。严格按照GB/T 10665-2004碳化钙国家标准出具质量评级、包装封签合规性及出厂放行结论。",
        "indicators": [
            {"deviation_name": "发气量偏差", "standard_ref": 305.0},
        ],
        "rules": [
            {
                "id": "carbide_grade_qualified",
                "name": "合格品发气量不低于260L/kg",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 260.0}]},
                "severity": "critical",
                "message": "成品电石发气量低于260L/kg，不合格品，禁止出厂"
            },
            {
                "id": "carbide_grade_first",
                "name": "一级品发气量不低于280L/kg",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 280.0}]},
                "severity": "warning",
                "message": "成品电石发气量低于280L/kg，不符合一级品标准，需降级处理"
            },
            {
                "id": "carbide_grade_excellent",
                "name": "优等品发气量不低于305L/kg",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 305.0}]},
                "severity": "info",
                "message": "成品电石发气量低于305L/kg，不符合优等品标准"
            }
        ]
    },
    {
        "id": "01M2N83BC35Q3X69A3G9ZKTRVJ",
        "name": "成品石灰质检工作流",
        "description": "石灰窑煅烧产出与筛分楼分级成品石灰，12项成品指标比对，评定高活性冶炼石灰品质等级，AI输出炉用分配建议。",
        "domains": ["quality"],
        "mat_name": "成品石灰",
        "mat_code": "01202511280001",
        "proj_type_code": 8,
        "sys_prompt": "你是石灰品质主管。分析成品石灰的活性度与粉末比率，判断是否满足特种电石炉高速冶炼需求，输出分仓配用方案。",
        "indicators": [
            {"deviation_name": "有效CaO偏差", "standard_ref": 90.0},
            {"deviation_name": "活性度偏差",  "standard_ref": 360.0},
        ],
        "rules": [
            {
                "id": "fin_lime_cao",
                "name": "有效CaO不低于90%",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 90.0}]},
                "severity": "critical",
                "message": "成品石灰有效CaO低于90%，不满足高活性冶炼石灰标准"
            },
            {
                "id": "fin_lime_activity",
                "name": "活性度不低于360ml",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 360.0}]},
                "severity": "warning",
                "message": "成品石灰活性度低于360ml，电石炉反应动力学条件恶化"
            },
            {
                "id": "fin_lime_dust",
                "name": "筛下粉率不高于8%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 8.0}]},
                "severity": "warning",
                "message": "成品石灰粉率偏高，增加炉内料层阻力，影响透气性"
            }
        ]
    },
    {
        "id": "01M2N83BJ87FH8XC5T0GXAR439",
        "name": "筛分焦炭质检工作流",
        "description": "筛分楼产出的各规格粒度分选焦炭（10项指标），剔除粉焦杂质，AI评估分选效率与料仓质量分布。",
        "domains": ["quality"],
        "mat_name": "筛分焦炭",
        "mat_code": "01202511280002",
        "proj_type_code": 2,
        "sys_prompt": "你是原料筛分车间主管。监控筛分楼振动筛筛网破损与焦炭粒度分布，防止细粉随焦炭入炉恶化炉膛透气性。",
        "indicators": [
            {"deviation_name": "粒度合格率偏差", "standard_ref": 85.0},
        ],
        "rules": [
            {
                "id": "screened_coke_size",
                "name": "粒度合格率不低于85%（10-30mm）",
                "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 85.0}]},
                "severity": "warning",
                "message": "筛分焦炭粒度合格率低于85%，需检查振动筛筛网"
            },
            {
                "id": "screened_coke_dust",
                "name": "筛下粉率不高于3%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 3.0}]},
                "severity": "warning",
                "message": "筛下粉率超标，细粉入炉恶化炉层透气性，电耗上升"
            },
            {
                "id": "screened_coke_moisture",
                "name": "水分不高于8%",
                "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 8.0}]},
                "severity": "info",
                "message": "筛分焦炭水分偏高，热耗增加"
            }
        ]
    }
]


EQUIPMENT_WORKFLOWS = [
    {
        "id": "01M2N83BRGJZTDYCTEG3DCQ580",
        "name": "110kV站主变压器监控工作流",
        "description": "针对全厂供电枢纽 110kV 1#、2#主变压器，巡检测点参数（油温、油位、套管、声音），AI诊断重载发热与绝缘风险。",
        "domains": ["alert"],
        "dev_name": "110kV变电站主变压器",
        "dev_code": "EQ-110KV-MAIN-TR",
        "dev_name_like": "变压器",
        "cycle_desc": "顶层油温不超85℃，温升限值，吸湿器硅胶变色巡查，局部放电检测，每小时巡检",
        "sys_prompt": "你是特高压变电站资深电气总工。根据油温油位与负荷特征，识别主变绝缘油劣化、过载温升超标及匝间短路征兆，给出降负荷或倒闸指令。",
        "rules": [
            {
                "id": "transformer_result",
                "name": "巡检结论无异常",
                "expression": {"op": "ne", "args": [{"field": "inspection_result"}, {"value": "异常"}]},
                "severity": "critical",
                "message": "主变压器巡检发现异常，需立即检查油温、油位及绝缘状态"
            },
            {
                "id": "transformer_status",
                "name": "设备运行状态正常",
                "expression": {"op": "ne", "args": [{"field": "device_status"}, {"value": "故障"}]},
                "severity": "critical",
                "message": "主变压器处于故障状态，需立即停机检修"
            }
        ]
    },
    {
        "id": "01M2N83BYVQ7KH2D8XNWMWJ45D",
        "name": "电石炉供电变压器监控工作流",
        "description": "监控1#、2#电石炉炉变、补偿变与C相短网变压器，监测大电流短网负荷、冷却水压力与温升，AI诊断三相平衡度。",
        "domains": ["alert"],
        "dev_name": "电石炉炉变及短网变压器",
        "dev_code": "EQ-CARBIDE-TR-01",
        "dev_name_like": "炉变",
        "cycle_desc": "三相二次电压、电流均衡，短网铜排温度不超65℃，冷却水进出水温差不超10℃，每小时巡检",
        "sys_prompt": "你是工业电弧炉供电专家。监控电石炉三相不平衡率与变压器重负荷热点，预防电极软着落翻料引起的瞬间大过流跳闸。",
        "rules": [
            {
                "id": "furnace_tr_result",
                "name": "炉变巡检结论无异常",
                "expression": {"op": "ne", "args": [{"field": "inspection_result"}, {"value": "异常"}]},
                "severity": "critical",
                "message": "电石炉变压器巡检异常，可能存在短网铜排过热或三相不平衡"
            },
            {
                "id": "furnace_tr_status",
                "name": "炉变运行状态正常",
                "expression": {"op": "ne", "args": [{"field": "device_status"}, {"value": "故障"}]},
                "severity": "critical",
                "message": "电石炉变压器故障停机，影响冶炼生产"
            }
        ]
    },
    {
        "id": "01M2N83C5Q0EVDGWDQF53FQ382",
        "name": "冶炼出炉机器人监控工作流",
        "description": "全生命周期跟踪 1#、2#炉出炉机器人机械轴承/链条润滑周期及烧穿器母线检修时限，AI诊断磨损并下发维保指令。",
        "domains": ["alert"],
        "dev_name": "冶炼出炉机器人",
        "dev_code": "EQ-FURNACE-ROBOT-01",
        "dev_name_like": "机器人",
        "cycle_desc": "机械臂关节轴承7天注3#锂基脂，大车行走轮7天，传动链条7天注机械油，烧穿器母线定期检修，每日巡检",
        "sys_prompt": "你是高温特种工业机器人维护专家。出炉环境伴随高辐射热与金属飞溅，严格考核7天注油周期，预防轴承干磨抱死或母线打火。",
        "rules": [
            {
                "id": "robot_inspection",
                "name": "机器人巡检结论无异常",
                "expression": {"op": "ne", "args": [{"field": "inspection_result"}, {"value": "异常"}]},
                "severity": "critical",
                "message": "出炉机器人巡检异常，请检查轴承润滑状态和传动链条"
            },
            {
                "id": "robot_status",
                "name": "机器人运行状态正常",
                "expression": {"op": "ne", "args": [{"field": "device_status"}, {"value": "故障"}]},
                "severity": "critical",
                "message": "出炉机器人处于故障状态，出炉作业中断"
            }
        ]
    },
    {
        "id": "01M2N83CC74XXN6BX09F024V5G",
        "name": "环形加料机运行监控工作流",
        "description": "监控 2#炉环形加料机主动轮、辅助轮15天润滑周期，及摆线针轮减速机运行油位，AI评估传动平稳度与卡堵风险。",
        "domains": ["alert"],
        "dev_name": "环形加料机",
        "dev_code": "EQ-CIRCULAR-FEEDER-02",
        "dev_name_like": "加料机",
        "cycle_desc": "环加主动轮/辅助轮15天注3#锂基脂，摆线针轮减速机CKC-220齿轮油，轨道间隙检查，每日巡检",
        "sys_prompt": "你是料仓物料输送机械工程师。环形加料机是炉顶连续密闭供料关键，严密监控润滑周期与轮缘磨损，防范料槽卡料扭断减速机轴。",
        "rules": [
            {
                "id": "feeder_inspection",
                "name": "加料机巡检结论无异常",
                "expression": {"op": "ne", "args": [{"field": "inspection_result"}, {"value": "异常"}]},
                "severity": "warning",
                "message": "环形加料机巡检异常，请检查润滑状态和减速机油位"
            },
            {
                "id": "feeder_status",
                "name": "加料机运行状态正常",
                "expression": {"op": "ne", "args": [{"field": "device_status"}, {"value": "故障"}]},
                "severity": "critical",
                "message": "环形加料机故障停机，炉顶供料中断"
            }
        ]
    },
    {
        "id": "01M2N83CJGRXG347725P4YKA3Y",
        "name": "电石炉液压站监控工作流",
        "description": "监控 1#、2#炉液压平台工作油压、油温、高压管路焊接支撑与定期检修时限，AI诊断内漏与液压失稳隐患。",
        "domains": ["alert"],
        "dev_name": "电石炉液压站及液压平台",
        "dev_code": "EQ-HYDRAULIC-STATION-01",
        "dev_name_like": "液压",
        "cycle_desc": "工作油压12-16MPa，系统油温30-55℃，蓄能器氮气压力检查，管路支撑焊缝定检，每小时巡检",
        "sys_prompt": "你是重型冶金液压系统高级专家。电极升降压放依赖液压缸精准动作，实时监控油温油压和管路开焊风险，防止高压油爆燃与翻炉事故。",
        "rules": [
            {
                "id": "hydraulic_inspection",
                "name": "液压站巡检结论无异常",
                "expression": {"op": "ne", "args": [{"field": "inspection_result"}, {"value": "异常"}]},
                "severity": "critical",
                "message": "液压站巡检异常，请立即检查油压（应12-16MPa）和油温（应30-55℃）"
            },
            {
                "id": "hydraulic_status",
                "name": "液压站运行状态正常",
                "expression": {"op": "ne", "args": [{"field": "device_status"}, {"value": "故障"}]},
                "severity": "critical",
                "message": "液压站故障，电极升降控制失效，存在翻炉安全风险"
            }
        ]
    },
    {
        "id": "01M2N83CRS0X3JWQEX6J5EDXVF",
        "name": "集控室中枢及DCS机柜巡检工作流",
        "description": "巡检集控室UPS电源后备供电能力，及循环水、石灰窑、原料三大DCS控制机柜通讯与供电，AI诊断系统级安全度。",
        "domains": ["alert"],
        "dev_name": "集控室中枢及DCS机柜间",
        "dev_code": "EQ-DCS-CENTRAL-01",
        "dev_name_like": "DCS",
        "cycle_desc": "UPS浮充电压380V±1%，蓄电池巡检，交换机通讯丢包率小于1%，机柜散热风扇，机柜温度不超28℃，每小时巡检",
        "sys_prompt": "你是工厂自动化与仪表电气高级总监。监控集控中枢全天候不间断供电与控制网络通讯状态，防止全厂DCS黑屏失去安全联锁保护。",
        "rules": [
            {
                "id": "dcs_inspection",
                "name": "DCS机柜巡检结论无异常",
                "expression": {"op": "ne", "args": [{"field": "inspection_result"}, {"value": "异常"}]},
                "severity": "critical",
                "message": "DCS集控机柜巡检异常，请检查机柜温度（应不超28℃）和通讯状态"
            },
            {
                "id": "dcs_status",
                "name": "DCS系统运行状态正常",
                "expression": {"op": "ne", "args": [{"field": "device_status"}, {"value": "故障"}]},
                "severity": "critical",
                "message": "DCS系统故障，全厂自动化联锁保护失效，需立即启动应急处置"
            }
        ]
    }
]


def fix_workflow(client, spec, graph, current_version):
    """Update workflow graph and republish."""
    wf_id = spec["id"]
    # PUT to save draft
    payload = {
        "name": spec["name"],
        "description": spec["description"],
        "domains": spec["domains"],
        "graph": graph,
        "version": current_version
    }
    updated = client.put(f"/api/v1/workflows/{wf_id}", payload)
    new_version = updated["data"]["version"]
    print(f"   Draft saved at version {new_version}")
    # Publish
    published = client.post(f"/api/v1/workflows/{wf_id}/publish", {"version": new_version})
    pub_rev = published["data"]["publishedRevision"]
    print(f"   Published at revision {pub_rev}")
    return pub_rev


def main():
    client = Client()
    print(f"Logged in. Workspace: {client.workspace_id}\n")
    print("=" * 60)
    print("Fixing 15 workflows: real DB queries + rule evaluation")
    print("=" * 60)

    fixed = []
    errors = []

    print("\n--- Quality Workflows (9) ---")
    for spec in QUALITY_WORKFLOWS:
        name = spec["name"]
        print(f"\n-> {name}")
        try:
            info = client.get(f"/api/v1/workflows/{spec['id']}")
            current_version = info["data"]["version"]
            print(f"   Current version: {current_version}")
            graph = build_quality_graph(
                mat_name=spec["mat_name"],
                mat_code=spec["mat_code"],
                proj_type_code=spec["proj_type_code"],
                indicators=spec["indicators"],
                rules=spec["rules"],
                sys_prompt=spec["sys_prompt"]
            )
            rev = fix_workflow(client, spec, graph, current_version)
            fixed.append({"name": name, "revision": rev})
        except Exception as e:
            print(f"   ERROR: {e}")
            errors.append({"name": name, "error": str(e)})

    print("\n--- Equipment Workflows (6) ---")
    for spec in EQUIPMENT_WORKFLOWS:
        name = spec["name"]
        print(f"\n-> {name}")
        try:
            info = client.get(f"/api/v1/workflows/{spec['id']}")
            current_version = info["data"]["version"]
            print(f"   Current version: {current_version}")
            graph = build_equipment_graph(
                dev_name=spec["dev_name"],
                dev_code=spec["dev_code"],
                dev_name_like=spec["dev_name_like"],
                rules=spec["rules"],
                sys_prompt=spec["sys_prompt"],
                cycle_desc=spec["cycle_desc"]
            )
            rev = fix_workflow(client, spec, graph, current_version)
            fixed.append({"name": name, "revision": rev})
        except Exception as e:
            print(f"   ERROR: {e}")
            errors.append({"name": name, "error": str(e)})

    print("\n" + "=" * 60)
    print(f"DONE: {len(fixed)} fixed, {len(errors)} errors")
    if errors:
        print("\nFailed:")
        for e in errors:
            print(f"  x {e['name']}: {e['error']}")
    print("\nSuccessfully fixed:")
    for f in fixed:
        print(f"  OK {f['name']} -> rev {f['revision']}")

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
