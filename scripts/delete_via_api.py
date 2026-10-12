# -*- coding: utf-8 -*-
"""Delete workflows, device alerts, and quality records via the platform API."""
import os
import sys
import time
import httpx
from sqlalchemy import text

sys.path.insert(0, os.path.abspath("backend"))
from app.db import get_engine

BASE_URL = "http://127.0.0.1:8011"


def get_token():
    """用写死的管理员账号登录本地 8011，返回访问令牌；失败时由 httpx 抛错（不做重试与降级）。"""
    with httpx.Client(timeout=30) as client:
        res = client.post(
            f"{BASE_URL}/api/auth/login",
            # 登录密码由运行环境提供，避免将本机凭据提交到仓库。
            json={"username": "workspace-admin", "password": os.environ["WORKSPACE_ADMIN_PASSWORD"]},
        )
        res.raise_for_status()
        return res.json()["token"]


def fetch_targets():
    """直连平台库列出各分类下**未删除**记录及其 version（删除接口需要版本号做乐观锁）。

    返回 {分类: [(id, version)]}；这里的连接只用于列出目标，真正的删除全部走 HTTP 接口，
    以便复用接口层的权限与审计逻辑（不直接 DELETE 数据）。
    """
    engine = get_engine()
    with engine.connect() as conn:
        # 1. Quality
        inspections = conn.execute(
            text("SELECT id, version FROM quality_inspections WHERE status != 'archived'")
        ).fetchall()
        templates = conn.execute(
            text("SELECT id, version FROM quality_templates WHERE status != 'deleted'")
        ).fetchall()
        metrics = conn.execute(
            text("SELECT id, version FROM quality_metrics WHERE status != 'deleted'")
        ).fetchall()

        # 2. Alerts
        advice = conn.execute(
            text("SELECT id, version FROM equipment_expert_advice WHERE status != 'deleted'")
        ).fetchall()
        points = conn.execute(
            text("SELECT id, version FROM equipment_measurement_points WHERE status != 'deleted'")
        ).fetchall()
        rules = conn.execute(
            text("SELECT id, version FROM equipment_alert_rules WHERE status != 'deleted'")
        ).fetchall()
        devices = conn.execute(
            text("SELECT id, version FROM equipment_devices WHERE status != 'deleted'")
        ).fetchall()

        # 3. Workflows
        workflows = conn.execute(
            text("SELECT id, version FROM workflow_definitions WHERE status != 'deleted'")
        ).fetchall()

    return {
        "quality_inspections": [(r[0], r[1]) for r in inspections],
        "quality_templates": [(r[0], r[1]) for r in templates],
        "quality_metrics": [(r[0], r[1]) for r in metrics],
        "alert_advice": [(r[0], r[1]) for r in advice],
        "alert_points": [(r[0], r[1]) for r in points],
        "alert_rules": [(r[0], r[1]) for r in rules],
        "alert_devices": [(r[0], r[1]) for r in devices],
        "workflows": [(r[0], r[1]) for r in workflows],
    }


def delete_batch(client, headers, category, endpoint_fmt, items):
    """按分类批量删除（串行、逐条带 version 调用 DELETE），返回 (成功数, 失败数)。

    单个失败只计数不中断（前 5 条失败才打印详情），每 200 条输出一次进度与速率。
    调用顺序在 main 中固定：先质检后预警最后工作流，避免外键/引用导致连带失败。
    """
    if not items:
        print(f"[{category}] No active items to delete.")
        return 0, 0

    print(f"[{category}] Starting deletion of {len(items)} items via API...")
    success = 0
    failed = 0
    start_t = time.time()

    for idx, (item_id, version) in enumerate(items, 1):
        url = endpoint_fmt.format(id=item_id, version=version)
        try:
            r = client.delete(url, headers=headers)
            if r.status_code in (200, 204):
                success += 1
            else:
                failed += 1
                if failed <= 5:
                    print(f"  [FAIL] {url} -> {r.status_code}: {r.text[:120]}")
        except Exception as exc:
            failed += 1
            if failed <= 5:
                print(f"  [EXC] {url} -> {exc}")

        if idx % 200 == 0 or idx == len(items):
            elapsed = time.time() - start_t
            rate = idx / elapsed if elapsed > 0 else 0
            print(f"  Progress: {idx}/{len(items)} ({rate:.1f} req/s), success={success}, failed={failed}")

    print(f"[{category}] Done: {success} succeeded, {failed} failed in {time.time() - start_t:.2f}s")
    return success, failed


def main():
    """清空并核对本地环境的业务数据：登录 -> 列出目标 -> 按固定顺序走接口删除 -> 复查剩余量。

    前置条件：后端在 127.0.0.1:8011 运行，且能连到本机平台数据库（get_engine 读 .env）；
    影响范围：真实删除质检/预警/工作流数据（不可撤销），因此仅用于本地/演示环境重置。
    复查阶段重新读库统计剩余未删除记录，用于确认接口删除是否完整生效。
    """
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    targets = fetch_targets()

    total_items = sum(len(v) for v in targets.values())
    print(f"Total active records to delete across all categories: {total_items}")

    limits = httpx.Limits(max_keepalive_connections=20, max_connections=30)
    with httpx.Client(base_url=BASE_URL, timeout=30, limits=limits) as client:
        # Step 1: Quality (inspections -> templates -> metrics)
        delete_batch(
            client, headers, "Quality Inspections",
            "/api/v1/quality/inspections/{id}?version={version}",
            targets["quality_inspections"]
        )
        delete_batch(
            client, headers, "Quality Templates",
            "/api/v1/quality/templates/{id}?version={version}",
            targets["quality_templates"]
        )
        delete_batch(
            client, headers, "Quality Metrics",
            "/api/v1/quality/metrics/{id}?version={version}",
            targets["quality_metrics"]
        )

        # Step 2: Alerts (advice -> points -> rules -> devices)
        delete_batch(
            client, headers, "Alert Advice",
            "/api/v1/alerts/advice/{id}?version={version}",
            targets["alert_advice"]
        )
        delete_batch(
            client, headers, "Alert Points",
            "/api/v1/alerts/points/{id}?version={version}",
            targets["alert_points"]
        )
        delete_batch(
            client, headers, "Alert Rules",
            "/api/v1/alerts/rules/{id}?version={version}",
            targets["alert_rules"]
        )
        delete_batch(
            client, headers, "Alert Devices",
            "/api/v1/alerts/devices/{id}?version={version}",
            targets["alert_devices"]
        )

        # Step 3: Workflows
        delete_batch(
            client, headers, "Workflows",
            "/api/v1/workflows/{id}?version={version}",
            targets["workflows"]
        )

    # Verification: check remaining active items
    remaining = fetch_targets()
    remaining_total = sum(len(v) for v in remaining.values())
    print(f"\nVerification: remaining active records after API deletion = {remaining_total}")
    for cat, items in remaining.items():
        print(f"  {cat}: {len(items)} active items remaining")


if __name__ == "__main__":
    main()
