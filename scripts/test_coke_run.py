"""一次性联调脚本：验证「焦炭进厂质检」工作流在本地平台能否跑通。

运行方式：后端已在 http://127.0.0.1:8011 运行（且平台可读本机 szh_new），直接
python scripts/test_coke_run.py；会先更新并发布该工作流的图，再触发一次真实运行。
影响范围：写库（工作流版本/发布记录与一次运行记录），其中 calculate/rule 节点只读数据，
confirmWrites=False 表示不产生业务写入。脚本内写死的账号、datasource id、工作流 id 均来自当时环境，
换环境必须替换；没有一个测试函数与断言，属于人工核对脚本，不要作为回归测试依赖。
"""
import os
import json
import uuid
import sys
import urllib.request

BASE_URL = "http://127.0.0.1:8011"
DATASOURCE_ID = "01M24QGNNN404ZF3A9XB48FRY5"
sys.stdout.reconfigure(encoding="utf-8")

req = urllib.request.Request(
    f"{BASE_URL}/api/auth/login",
    # 登录密码由运行环境提供，避免将本机凭据提交到仓库。
    data=json.dumps({"username": "workspace-admin", "password": os.environ["WORKSPACE_ADMIN_PASSWORD"]}).encode(),
    headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(req) as r:
    d = json.loads(r.read().decode())
    token = d["token"]
    wid = d["workspaces"][0]["id"]

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "X-Workspace-Id": wid
}

coke_sql = """SELECT
  COALESCE(d.c_jianyanjieguo, CAST(NULLIF(d.c_testresults, '') AS DECIMAL(18,4))) AS xice_value,
  p.c_projectName AS indicator_name,
  p.c_standardMin AS std_min,
  p.c_standardMax AS std_max,
  p.c_judgeRule AS judge_rule,
  p.c_shifoushizhuyaopanduanbiaozhun AS is_critical,
  f.c_incomingQcNo AS qc_no,
  f.c_samplingTime AS sample_time,
  f.c_testResults AS qc_result
FROM t_incomingmaterialqcform f
JOIN t_qualityinspectionitemdetails d ON d.mdIncomingMaterialQcFormId = f.incomingMaterialQcFormId
JOIN t_qualityinspectionproject p ON p.qualityInspectionProjectId = d.c_qualityInspectionProject
WHERE f.isDeleted = 0 AND d.isDeleted = 0 AND p.isDeleted = 0
  AND f.c_qualityInspectionTemplate = '0001328-199815f9e9f044218f663c79f9938b36'
  AND (d.c_jianyanjieguo IS NOT NULL OR (d.c_testresults IS NOT NULL AND d.c_testresults != ''))
ORDER BY f.autoId DESC LIMIT 15"""

graph = {
    "nodes": [
        {
            "id": "start", "kind": "start", "title": "开始", "position": {"x": 50, "y": 200},
            "config": {"variables": []}
        },
        {
            "id": "read_indicators", "kind": "database-query", "title": "读取焦炭化验指标实测值",
            "position": {"x": 320, "y": 200},
            "config": {"datasourceId": DATASOURCE_ID, "query": coke_sql, "parameters": {}, "maxRows": 20}
        },
        {
            "id": "calc_deviations", "kind": "calculate-values", "title": "计算各项指标基准偏差",
            "position": {"x": 680, "y": 200},
            "config": {
                "calculations": [
                    {"name": "核查时间", "operation": "now", "left": {"source": "constant", "value": ""}},
                    {"name": "材料名称", "operation": "copy", "left": {"source": "constant", "value": "焦炭"}},
                    {"name": "固定碳基准偏差", "operation": "subtract", "left": {"source": "field", "field": "xice_value"}, "right": {"source": "constant", "value": 82.0}},
                    {"name": "灰分基准偏差", "operation": "subtract", "left": {"source": "field", "field": "xice_value"}, "right": {"source": "constant", "value": 12.0}}
                ]
            }
        },
        {
            "id": "rule_check", "kind": "rule-evaluate", "title": "规则评估 · 焦炭国标符合度判断",
            "position": {"x": 1040, "y": 200},
            "config": {
                "mode": "quality",
                "rules": [
                    {
                        "id": "coke_fc", "name": "固定碳合规性",
                        "expression": {"op": "gte", "args": [{"field": "xice_value"}, {"value": 82.0}]},
                        "severity": "critical", "message": "焦炭固定碳实测偏低"
                    },
                    {
                        "id": "coke_ash", "name": "灰分指标限值",
                        "expression": {"op": "lte", "args": [{"field": "xice_value"}, {"value": 13.5}]},
                        "severity": "critical", "message": "焦炭灰分超出上限"
                    }
                ]
            }
        },
        {
            "id": "ai_diagnosis", "kind": "llm", "title": "AI智能品质诊断与处置决策",
            "position": {"x": 1400, "y": 200},
            "config": {
                "modelProfile": "reasoning",
                "systemPrompt": "你是电石冶炼焦炭原料资深质检专家。严格根据实测数据与规则评估结果给出质量评价与放行处置建议。",
                "promptTemplate": (
                    "【焦炭质检化验与规则评估摘要】：\n"
                    "- 规则判定概要：{{ $.summary }}\n"
                    "- 触发问题清单：{{ $.issues }}\n"
                    "- 实测化验指标：{{ $.data }}\n\n"
                    "请输出专业评审意见：\n"
                    "1. 【品质合规评估】（必检指标符合性）\n"
                    "2. 【炉况工艺影响】（电极比电阻、炉渣量、还原活性）\n"
                    "3. 【现场处置指令】（合格放行 / 降级扣款 / 拒收退货）"
                ),
                "temperature": 0.1
            }
        },
        {
            "id": "end", "kind": "end", "title": "结束", "position": {"x": 1760, "y": 200},
            "config": {}
        }
    ],
    "edges": [
        {"id": "e1", "source": "start", "target": "read_indicators", "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e2", "source": "read_indicators", "target": "calc_deviations", "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e3", "source": "calc_deviations", "target": "rule_check", "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e4", "source": "rule_check", "target": "ai_diagnosis", "port": "out", "sourceSide": "right", "targetSide": "left"},
        {"id": "e5", "source": "ai_diagnosis", "target": "end", "port": "out", "sourceSide": "right", "targetSide": "left"}
    ]
}

coke_id = "01M2N839WPPP2EKAH0TG99V4TC"
r_get = urllib.request.Request(f"{BASE_URL}/api/v1/workflows/{coke_id}", headers=headers)
with urllib.request.urlopen(r_get) as resp:
    wf = json.loads(resp.read().decode())["data"]
    v = wf["version"]

r_put = urllib.request.Request(
    f"{BASE_URL}/api/v1/workflows/{coke_id}",
    data=json.dumps({"name": wf["name"], "description": wf["description"], "domains": wf["domains"], "graph": graph, "version": v}).encode(),
    headers=headers, method="PUT"
)
with urllib.request.urlopen(r_put) as resp:
    v_new = json.loads(resp.read().decode())["data"]["version"]

r_pub = urllib.request.Request(
    f"{BASE_URL}/api/v1/workflows/{coke_id}/publish",
    data=json.dumps({"version": v_new}).encode(), headers=headers
)
with urllib.request.urlopen(r_pub) as resp:
    pub = json.loads(resp.read().decode())["data"]
    rev = pub["publishedRevision"]
    print(f"Coke workflow published at rev {rev}", flush=True)

print("Executing workflow...", flush=True)
r_run = urllib.request.Request(
    f"{BASE_URL}/api/v1/workflows/{coke_id}/runs",
    data=json.dumps({
        "version": rev, "mode": "published",
        "idempotencyKey": f"coke-test-{uuid.uuid4().hex[:8]}",
        "inputs": {}, "confirmWrites": False, "timeoutSeconds": 300
    }).encode(), headers=headers
)

with urllib.request.urlopen(r_run, timeout=310) as resp:
    res = json.loads(resp.read().decode())
    d = res.get("data", {})
    print("Execution Status:", d.get("status"))
    nodes = d.get("result", {}).get("nodes", {})
    for nk, nv in nodes.items():
        print(f"  Node {nk}: status={nv.get('status')}")
    rc = nodes.get("rule_check", {}).get("output", {})
    print("  Rule check summary:", rc.get("summary"))
    print("  Rule check issues count:", len(rc.get("issues", [])))
    ai = nodes.get("ai_diagnosis", {}).get("output", {})
    content = ai.get("content", "")
    print(f"  AI diagnosis output length: {len(content)} chars")
    if content:
        print("\n=== AI Diagnosis Sample Output ===")
        print(content[:500])
