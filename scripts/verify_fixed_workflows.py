"""Verify that the fixed workflows run correctly and produce rule evaluation output."""
import os
import json
import uuid
import urllib.request

BASE_URL = "http://127.0.0.1:8011"

class Client:
    def __init__(self):
        req = urllib.request.Request(
            f"{BASE_URL}/api/auth/login",
            # 登录密码由运行环境提供，避免将本机凭据提交到仓库。
            data=json.dumps({"username": "workspace-admin", "password": os.environ["WORKSPACE_ADMIN_PASSWORD"]}).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as r:
            d = json.loads(r.read().decode())
            self.token = d["token"]
            self.workspace_id = d["workspaces"][0]["id"]
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-Workspace-Id": self.workspace_id
        }

    def post(self, path, payload):
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=json.dumps(payload).encode(),
            headers=self.headers
        )
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except urllib.request.urllib.error.HTTPError as e:
            print(f"HTTP {e.code}:", e.read().decode())
            raise

    def get(self, path):
        req = urllib.request.Request(f"{BASE_URL}{path}", headers=self.headers)
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())

client = Client()
print(f"Workspace: {client.workspace_id}\n")

# Test 1: Quality workflow - 焦炭 (rev 4)
print("=" * 55)
print("Test 1: 焦炭进厂质检工作流 (rev 4)")
print("=" * 55)
coke_id = "01M2N839WPPP2EKAH0TG99V4TC"
run1 = client.post(f"/api/v1/workflows/{coke_id}/runs", {
    "version": 4,
    "mode": "published",
    "idempotencyKey": f"verify-coke-{uuid.uuid4().hex[:8]}",
    "inputs": {},
    "confirmWrites": False,
    "timeoutSeconds": 90
})
d1 = run1.get("data", {})
print(f"Status:  {d1.get('status')}")
nodes_out = d1.get("result", {}).get("nodes", {})

# Check database-query node output
db_node = nodes_out.get("read_indicators", {})
db_rows = db_node.get("output", {}).get("data", [])
print(f"read_indicators rows returned: {len(db_rows)}")
if db_rows:
    sample = db_rows[0]
    print(f"  Sample row keys: {list(sample.keys())}")
    print(f"  Sample values: indicator_name={sample.get('indicator_name')}, xice_value={sample.get('xice_value')}, std_min={sample.get('std_min')}, std_max={sample.get('std_max')}")

# Check rule_check node output
rule_node = nodes_out.get("rule_check", {})
rule_out = rule_node.get("output", {})
issues = rule_out.get("issues", [])
passed = rule_out.get("passed")
print(f"rule_check passed: {passed}, issues: {len(issues)}")
for iss in issues[:3]:
    print(f"  Issue: [{iss.get('severity')}] {iss.get('ruleName')} - {iss.get('message', '')[:60]}")

# Check AI output
ai_node = nodes_out.get("ai_diagnosis", {})
ai_content = ai_node.get("output", {}).get("content", "")
if ai_content:
    print(f"ai_diagnosis output (first 200 chars): {ai_content[:200]}...")
else:
    print("ai_diagnosis: no content (LLM may be pending or not configured)")

# Test 2: Equipment workflow - 出炉机器人 (rev 4)
print("\n" + "=" * 55)
print("Test 2: 冶炼出炉机器人监控工作流 (rev 4)")
print("=" * 55)
robot_id = "01M2N83C5Q0EVDGWDQF53FQ382"
run2 = client.post(f"/api/v1/workflows/{robot_id}/runs", {
    "version": 4,
    "mode": "published",
    "idempotencyKey": f"verify-robot-{uuid.uuid4().hex[:8]}",
    "inputs": {},
    "confirmWrites": False,
    "timeoutSeconds": 90
})
d2 = run2.get("data", {})
print(f"Status:  {d2.get('status')}")
nodes_out2 = d2.get("result", {}).get("nodes", {})

db_node2 = nodes_out2.get("read_inspections", {})
db_rows2 = db_node2.get("output", {}).get("data", [])
print(f"read_inspections rows returned: {len(db_rows2)}")
if db_rows2:
    sample2 = db_rows2[0]
    print(f"  Sample row keys: {list(sample2.keys())}")
    print(f"  inspection_item={sample2.get('inspection_item')}, inspection_result={sample2.get('inspection_result')}, device_status={sample2.get('device_status')}")

rule_node2 = nodes_out2.get("rule_check", {})
rule_out2 = rule_node2.get("output", {})
issues2 = rule_out2.get("issues", [])
passed2 = rule_out2.get("passed")
print(f"rule_check passed: {passed2}, issues: {len(issues2)}")
for iss in issues2[:3]:
    print(f"  Issue: [{iss.get('severity')}] {iss.get('ruleName')} - {iss.get('message', '')[:60]}")

ai_node2 = nodes_out2.get("ai_diagnosis", {})
ai_content2 = ai_node2.get("output", {}).get("content", "")
if ai_content2:
    print(f"ai_diagnosis output (first 200 chars): {ai_content2[:200]}...")
else:
    print("ai_diagnosis: no content")

print("\nAll tests complete.")
