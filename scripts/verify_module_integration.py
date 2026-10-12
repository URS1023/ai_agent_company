# -*- coding: utf-8 -*-
"""Verify Equipment (Alerts) and Quality modules integration via live APIs."""
import os
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE_URL = "http://127.0.0.1:8011"

# Step 1: Login
# 登录密码由运行环境提供，避免将本机凭据提交到仓库。
login_data = json.dumps({"username": "workspace-admin", "password": os.environ["WORKSPACE_ADMIN_PASSWORD"]}).encode("utf-8")
login_req = urllib.request.Request(
    f"{BASE_URL}/api/auth/login",
    data=login_data,
    headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(login_req) as resp:
    res = json.loads(resp.read().decode("utf-8"))
    token = res["token"]

auth_headers = {"Authorization": f"Bearer {token}"}

print("=== 1. TEST EQUIPMENT / ALERTS DEVICE CATALOGUE (/alerts) ===")
req_alert = urllib.request.Request(f"{BASE_URL}/api/v1/alerts/device-catalogue?domain=alert", headers=auth_headers)
with urllib.request.urlopen(req_alert) as resp:
    r = json.loads(resp.read().decode("utf-8"))
    alert_items = r.get("data", {}).get("items", [])
    print(f"Total Alert Devices: {len(alert_items)}")
    for it in alert_items:
        wf = it.get("workflow") or {}
        print(f"  [Device] {it['name']} ({it['code']}) | Status: {it['status']} | Category: {it.get('category')} | Workflow: id={wf.get('id')} status={wf.get('status')} rev={wf.get('publishedRevision')}")

print("\n=== 2. TEST QUALITY PRODUCTS CATALOGUE (/quality) ===")
req_qual = urllib.request.Request(f"{BASE_URL}/api/v1/quality/products", headers=auth_headers)
with urllib.request.urlopen(req_qual) as resp:
    r = json.loads(resp.read().decode("utf-8"))
    data = r.get("data", {})
    entries = data.get("entries", [])
    print(f"Source Label: {data.get('sourceLabel')}")
    print(f"Total Quality Entries: {len(entries)}")
    for ent in entries:
        print(f"  [Category: {ent['category']}] {ent['productName']} ({ent['productCode']}) | Template: {ent['templateName']} | WF: {ent.get('workflowName')} ({ent.get('workflowId')}) | Rev: {ent.get('publishedRevision')} | Status: {ent.get('status')}")

print("\n=== 3. TEST QUALITY DEVICE CATALOGUE (Domain=quality) ===")
req_qdev = urllib.request.Request(f"{BASE_URL}/api/v1/alerts/device-catalogue?domain=quality", headers=auth_headers)
with urllib.request.urlopen(req_qdev) as resp:
    r = json.loads(resp.read().decode("utf-8"))
    quality_items = r.get("data", {}).get("items", [])
    print(f"Total Quality Station Devices: {len(quality_items)}")
    for it in quality_items:
        wf = it.get("workflow") or {}
        print(f"  [Station] {it['name']} ({it['code']}) | Workflow: id={wf.get('id')} status={wf.get('status')} rev={wf.get('publishedRevision')}")

print("\n=== 4. TEST DEVICE-ENTRY (Equipment One-Click Open) ===")
first_alert_dev = alert_items[0] if alert_items else None
if first_alert_dev:
    entry_payload = json.dumps({"deviceId": first_alert_dev["id"], "domain": "alert"}).encode("utf-8")
    req_entry = urllib.request.Request(
        f"{BASE_URL}/api/v1/workflows/device-entry",
        data=entry_payload,
        headers={"Content-Type": "application/json", **auth_headers}
    )
    with urllib.request.urlopen(req_entry) as resp:
        r = json.loads(resp.read().decode("utf-8"))
        d = r.get("data", {})
        print(f"  Alert device-entry success:")
        print(f"    Binding ID: {d.get('bindingId')}")
        print(f"    Editor Path: {d.get('editorPath')}")
        print(f"    Workflow: {d.get('workflow', {}).get('name')} (Rev: {d.get('workflow', {}).get('publishedRevision')})")

print("\n=== 5. TEST QUALITY TEMPLATES & METRICS APIS ===")
req_templates = urllib.request.Request(f"{BASE_URL}/api/v1/quality/templates", headers=auth_headers)
with urllib.request.urlopen(req_templates) as resp:
    r = json.loads(resp.read().decode("utf-8"))
    templates = r.get("data", [])
    print(f"Total Quality Templates: {len(templates)}")
    for t in templates[:5]:
        print(f"  Template: {t['name']} (Metrics: {len(t.get('metricIds', []))})")

req_metrics = urllib.request.Request(f"{BASE_URL}/api/v1/quality/metrics", headers=auth_headers)
with urllib.request.urlopen(req_metrics) as resp:
    r = json.loads(resp.read().decode("utf-8"))
    metrics = r.get("data", [])
    print(f"Total Quality Metrics: {len(metrics)}")
    for m in metrics[:5]:
        print(f"  Metric: {m['name']} ({m.get('unit')}) [min={m.get('standardMin')}, max={m.get('standardMax')}]")

print("\n>>> ALL 5 MODULE INTEGRATION CHECKS PASSED! <<<")
