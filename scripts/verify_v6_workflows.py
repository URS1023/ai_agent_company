"""定点验证脚本：触发两个已发布工作流（焦炭质检、冶炼出炉机器人）的 v6 版本并打印节点结果。

运行方式：后端需在 http://127.0.0.1:8011 运行且这两个工作流已发布 v6，然后
python scripts/verify_v6_workflows.py；会真实执行工作流（写运行记录，可能调用模型）。
影响范围：只读业务数据 + 产生运行记录（confirmWrites=False）。工作流 id 与口令写死自当时环境，
换环境必须替换，属于人工联调脚本而非自动化测试。
"""
import os
import json
import uuid
import sys
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8011"
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


def post(path, payload):
    """带登录令牌与工作区头的 POST；HTTP 错误时打印状态码与响应体后原样抛出，便于人工定位失败原因。"""
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode(),
        headers=headers
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode())
        raise


print("Test 1: 焦炭 quality workflow (rev 6)")
coke_id = "01M2N839WPPP2EKAH0TG99V4TC"
run = post("/api/v1/workflows/" + coke_id + "/runs", {
    "version": 6, "mode": "published",
    "idempotencyKey": "v6-coke-" + uuid.uuid4().hex[:8],
    "inputs": {}, "confirmWrites": False, "timeoutSeconds": 90
})
d = run.get("data", {})
status = d.get("status")
print("  Status:", status)
nodes = d.get("result", {}).get("nodes", {})

db_out = nodes.get("read_indicators", {}).get("output", {})
rows = db_out.get("data", [])
print("  read_indicators rows:", len(rows))
if rows:
    print("  Sample row:", rows[0])

rc_out = nodes.get("rule_check", {}).get("output", {})
passed = rc_out.get("passed")
issues = rc_out.get("issues", [])
print("  rule_check passed=" + str(passed) + ", issues=" + str(len(issues)))
for iss in issues[:3]:
    sev = iss.get("severity", "")
    name = iss.get("ruleName", "")
    msg = iss.get("message", "")[:60]
    print("    [" + sev + "] " + name + " - " + msg)

ai_content = nodes.get("ai_diagnosis", {}).get("output", {}).get("content", "")
print("  AI output length:", len(ai_content), "chars")
if ai_content:
    print("  AI first 200 chars:", ai_content[:200])

print()
print("Test 2: 冶炼出炉机器人 equipment workflow (rev 6)")
robot_id = "01M2N83C5Q0EVDGWDQF53FQ382"
run2 = post("/api/v1/workflows/" + robot_id + "/runs", {
    "version": 6, "mode": "published",
    "idempotencyKey": "v6-robot-" + uuid.uuid4().hex[:8],
    "inputs": {}, "confirmWrites": False, "timeoutSeconds": 90
})
d2 = run2.get("data", {})
status2 = d2.get("status")
print("  Status:", status2)
nodes2 = d2.get("result", {}).get("nodes", {})

db2 = nodes2.get("read_inspections", {}).get("output", {})
rows2 = db2.get("data", [])
print("  read_inspections rows:", len(rows2))
if rows2:
    print("  Sample row:", rows2[0])

rc2 = nodes2.get("rule_check", {}).get("output", {})
passed2 = rc2.get("passed")
issues2 = rc2.get("issues", [])
print("  rule_check passed=" + str(passed2) + ", issues=" + str(len(issues2)))

ai2 = nodes2.get("ai_diagnosis", {}).get("output", {}).get("content", "")
print("  AI output length:", len(ai2), "chars")
if ai2:
    print("  AI first 200 chars:", ai2[:200])

print()
print("Verification complete!")
