"""Check actual c_projectType values in t_qualityinspectionproject and t_incomingmaterialqcform
to find the correct filter values for the JOIN SQL."""
import os
import json
import urllib.request

BASE_URL = "http://127.0.0.1:8011"
DATASOURCE_ID = "01M24QGNNN404ZF3A9XB48FRY5"


class Client:
    """最小查询客户端：登录本地平台后固定用写死的 datasource 发起只读查询。"""
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

    def query(self, sql):
        """经平台接口执行只读 SQL（固定 maxRows=200）；HTTP 错误打印状态码后原样抛出。"""
        payload = {
            "datasourceId": DATASOURCE_ID,
            "query": sql,
            "parameters": {},
            "maxRows": 200
        }
        req = urllib.request.Request(
            f"{BASE_URL}/api/v1/datasources/{DATASOURCE_ID}/query",
            data=json.dumps(payload).encode(),
            headers=self.headers
        )
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except urllib.request.urllib.error.HTTPError as e:
            print("HTTP Error:", e.code, e.read().decode())
            raise


client = Client()

print("=== t_qualityinspectionproject: distinct c_projectType values ===")
r1 = client.query(
    "SELECT DISTINCT c_projectType, COUNT(*) as cnt "
    "FROM t_qualityinspectionproject "
    "WHERE isDeleted=0 "
    "GROUP BY c_projectType "
    "ORDER BY cnt DESC LIMIT 30"
)
rows1 = r1.get("data", r1.get("rows", []))
for row in rows1:
    print(row)

print()
print("=== t_qualityinspectionproject: sample rows with names and types ===")
r2 = client.query(
    "SELECT c_projectType, c_projectName, c_standardMin, c_standardMax, c_judgeRule "
    "FROM t_qualityinspectionproject "
    "WHERE isDeleted=0 "
    "LIMIT 30"
)
rows2 = r2.get("data", r2.get("rows", []))
for row in rows2:
    print(row)

print()
print("=== t_incomingmaterialqcform: c_lailiaozhijianfenlei distinct values ===")
r3 = client.query(
    "SELECT DISTINCT c_lailiaozhijianfenlei, COUNT(*) as cnt "
    "FROM t_incomingmaterialqcform "
    "WHERE isDeleted=0 "
    "GROUP BY c_lailiaozhijianfenlei "
    "ORDER BY cnt DESC LIMIT 20"
)
rows3 = r3.get("data", r3.get("rows", []))
for row in rows3:
    print(row)

print()
print("=== t_incomingmaterialqcform: sample of recent rows ===")
r4 = client.query(
    "SELECT incomingMaterialQcFormId, c_incomingQcNo, c_lailiaozhijianfenlei, c_testResults, c_samplingTime "
    "FROM t_incomingmaterialqcform "
    "WHERE isDeleted=0 "
    "ORDER BY autoId DESC LIMIT 10"
)
rows4 = r4.get("data", r4.get("rows", []))
for row in rows4:
    print(row)

print()
print("=== t_qualityinspectionitemdetails: sample rows (check FK) ===")
r5 = client.query(
    "SELECT d.autoId, d.mdIncomingMaterialQcFormId, d.c_qualityInspectionProject, d.c_testresults "
    "FROM t_qualityinspectionitemdetails d "
    "WHERE d.isDeleted=0 "
    "ORDER BY d.autoId DESC LIMIT 10"
)
rows5 = r5.get("data", r5.get("rows", []))
for row in rows5:
    print(row)

print()
print("=== JOIN test: recent form -> details -> project ===")
r6 = client.query(
    "SELECT f.c_incomingQcNo, p.c_projectType, p.c_projectName, d.c_testresults, p.c_standardMin, p.c_standardMax "
    "FROM t_incomingmaterialqcform f "
    "JOIN t_qualityinspectionitemdetails d ON d.mdIncomingMaterialQcFormId = f.incomingMaterialQcFormId "
    "JOIN t_qualityinspectionproject p ON p.autoId = d.c_qualityInspectionProject "
    "WHERE f.isDeleted=0 AND d.isDeleted=0 AND p.isDeleted=0 "
    "ORDER BY f.autoId DESC LIMIT 20"
)
rows6 = r6.get("data", r6.get("rows", []))
print(f"JOIN returned {len(rows6)} rows")
for row in rows6:
    print(row)
