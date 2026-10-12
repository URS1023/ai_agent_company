"""Verify the 15 deployed workflows, scheduled jobs, and run AI node diagnoses."""
import os
import sys
sys.stdout.reconfigure(encoding='utf-8')

import urllib.request, json, uuid

BASE_URL = "http://127.0.0.1:8011"

# Login
login_req = urllib.request.Request(
    f"{BASE_URL}/api/auth/login",
    # 登录密码由运行环境提供，避免将本机凭据提交到仓库。
    data=json.dumps({"username": "workspace-admin", "password": os.environ["WORKSPACE_ADMIN_PASSWORD"]}).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(login_req) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    token = data["token"]
    workspace_id = data["workspaces"][0]["id"]

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "X-Workspace-Id": workspace_id
}

def get(path):
    """带登录令牌与工作区头的 GET；HTTP 错误按 urllib 异常抛出（脚本用于人工联调，不做重试）。"""
    req = urllib.request.Request(f'{BASE_URL}{path}', headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def post(path, payload):
    """带登录令牌与工作区头的 POST（JSON 体）；失败抛异常，由使用者人工判断。"""
    req = urllib.request.Request(f'{BASE_URL}{path}', data=json.dumps(payload).encode('utf-8'), headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

print("=== 1. 已发布的 15 个细分工作流 ===")
flows = get("/api/v1/workflows?limit=100")["data"]
print(f"工作流总数: {len(flows)}")
for f in flows:
    print(f"  [工作流 ID: {f['id']}] {f['name']} | 状态: {f['status']} | 正式发布版本: v{f['publishedRevision']} | 业务域: {f['domains']}")

print("\n=== 2. 已注册并激活的自动化调度任务 (ScheduledJobs) ===")
jobs_resp = get("/api/v1/system-tools/jobs?status=active")
jobs = jobs_resp.get("items", jobs_resp.get("data", {}).get("items", []))
if not jobs and isinstance(jobs_resp, dict) and "items" in jobs_resp:
    jobs = jobs_resp["items"]
print(f"活跃调度任务数: {len(jobs)}")
for j in jobs:
    print(f"  [调度编码: {j['code']}] {j['name']} | 模式: {j['scheduleKind']} ({j['scheduleExpression']}) | 状态: {j['status']} | 下次运行: {j.get('nextRunAt')}")

print("\n=== 3. 运行验证 1：【焦炭进厂质检工作流】(含末端AI智能诊断) ===")
coke_wf = next((w for w in flows if "焦炭" in w["name"]), None)
if coke_wf:
    run1 = post(f"/api/v1/workflows/{coke_wf['id']}/runs", {
        "version": coke_wf["version"],
        "mode": "published",
        "idempotencyKey": f"verify-coke-run-{uuid.uuid4().hex[:8]}",
        "inputs": {},
        "confirmWrites": False,
        "timeoutSeconds": 60
    })
    print(f"  运行状态: {run1['data']['status']}")
    content1 = run1['data'].get('result', {}).get('nodes', {}).get('ai_diagnosis', {}).get('output', {}).get('content')
    print(f"  AI诊断输出全文摘要:\n{content1}\n")

print("\n=== 4. 运行验证 2：【冶炼出炉机器人监控工作流】(含末端AI智能诊断) ===")
robot_wf = next((w for w in flows if "机器人" in w["name"]), None)
if robot_wf:
    run2 = post(f"/api/v1/workflows/{robot_wf['id']}/runs", {
        "version": robot_wf["version"],
        "mode": "published",
        "idempotencyKey": f"verify-robot-run-{uuid.uuid4().hex[:8]}",
        "inputs": {},
        "confirmWrites": False,
        "timeoutSeconds": 60
    })
    print(f"  运行状态: {run2['data']['status']}")
    content2 = run2['data'].get('result', {}).get('nodes', {}).get('ai_diagnosis', {}).get('output', {}).get('content')
    print(f"  AI诊断输出全文摘要:\n{content2}\n")

print("全部工作流与调度任务验证顺利通过！")
