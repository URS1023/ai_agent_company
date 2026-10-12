"""一键验证 AI 复合交付链路；只使用合成数据、临时 SQLite 和本机模型桩。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="仅在已单独完成前端构建时使用")
    args = parser.parse_args()
    node, npm = shutil.which("node"), shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        raise SystemExit("需要已安装的 Node 和 npm；不会自动安装或跳过检查。")
    evidence_root = ROOT / ".implementation/quality-gates/chat-delivery"
    # 每轮独立保存日志和浏览器产物，复测不能覆盖上一轮失败或通过证据。
    output = evidence_root / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    output.mkdir(parents=True)
    phases = [("python-syntax", [sys.executable, "backend/compile_check.py"]),
        # 大版本批次十九：先验证回执原子末段、撤权及旧库显式迁移，再执行原业务回归。
        ("backend-receipt-contracts", [sys.executable, "tests/run_isolated_backend.py",
            "tests/backend/test_durable_tool_receipts.py", "tests/backend/test_receipt_migration.py",
            "tests/backend/test_receipt_schema_contract.py",
            "tests/backend/test_receipt_platform_migration.py",
            "--disable-warnings"]),
        ("backend-deliveries", [sys.executable, "tests/run_isolated_backend.py",
            "tests/backend/test_task_clarification_delivery.py", "tests/backend/test_business_worker.py",
            "tests/backend/test_dsh_query_execution.py",
            "tests/backend/test_dsh_dashboard_delivery.py", "tests/backend/test_template_query_snapshot.py",
            "tests/backend/test_template_snapshot_matrix.py",
            "tests/backend/test_openwebui_query_dashboard.py", "tests/backend/test_query_delivery.py",
            "tests/backend/test_query_presentation.py", "tests/backend/test_query_presentation_report.py",
            "tests/backend/test_dsh_query_delivery_resume.py", "tests/backend/test_dsh_multiple_presentation_drafts.py",
            "tests/backend/test_presentation_request_pages.py", "tests/backend/test_presentation_partial_batches.py",
            "tests/backend/test_presentation_intent_integrity.py",
            "--disable-warnings"]),
        # 随合同增长按业务域分组，保留每阶段 600 秒预算；不删除用例或扩大超时。
        ("backend-platform-contracts", [sys.executable, "tests/run_isolated_backend.py",
            # 对话使用的工具、模型权限与知识正文也进入同一交付门禁。
            "tests/backend/test_tool_schema_contract.py", "tests/backend/test_runtime_tool_registration.py",
            "tests/backend/test_dsh_presentation_schema.py",
            "tests/backend/test_connector_derivation_contract.py",
            "tests/backend/test_model_and_workspace_permission_edges.py",
            "tests/backend/test_auth_token_logout.py",
            "tests/backend/test_knowledge_search_authority.py", "tests/backend/test_file_template_font_units.py",
            # 历史数据撤权、Bot 客户端上限和共享知识范围也属于对话实际交付契约。
            "tests/backend/test_analytics_snapshot_authorization.py",
            "tests/backend/test_analytics_chart_snapshot_scope.py",
            "tests/backend/test_analytics_dsh_and_export_contract.py",
            "tests/backend/test_analytics_numeric_precision.py",
            "tests/backend/test_bot_runtime_authorization.py", "tests/backend/test_knowledge_tool_scope.py",
            # 大版本批次十四：传输精度、配置竞态、图谱属性和会话终态。
            "tests/backend/test_business_numeric_wire.py", "tests/backend/test_analytics_config_lifecycle.py",
            "tests/backend/test_dsh_completion_contract.py",
            "tests/backend/test_runtime_recovery.py", "--disable-warnings"]),
        # 大版本批次十八：复合任务与自然续办独立计时，原用例迁移后仍全部执行。
        ("backend-task-continuation", [sys.executable, "tests/run_isolated_backend.py",
            "tests/backend/test_dsh_compound_contract.py", "tests/backend/test_dsh_compound_boundaries.py",
            "tests/backend/test_dsh_task_operation_guard.py", "tests/backend/test_dsh_task_datasource_halt.py",
            "tests/backend/test_dsh_halted_task_saved_deliveries.py",
            "tests/backend/test_dsh_natural_continuation.py", "tests/backend/test_dsh_artifact_revisions.py", "--disable-warnings"]),
        # 大版本批次二十三：375 项数据合同按职责拆分，保留全部入口及每阶段 600 秒预算。
        ("backend-query-lifecycle", [sys.executable, "tests/run_isolated_backend.py",
            "tests/backend/test_query_lifecycle_control.py", "tests/backend/test_query_lifecycle_governance.py",
            "tests/backend/test_dsh_query_cancellation.py", "tests/backend/test_database_provider.py",
            "tests/backend/test_datasource_governance.py", "tests/backend/test_analytics_task_control.py",
            "tests/backend/test_analytics.py", "tests/backend/test_semantic_relation_contract.py",
            "tests/backend/test_data_profile.py", "tests/backend/test_read_connection_pool.py",
            "--durations=15", "--disable-warnings"]),
        ("backend-graph-extraction", [sys.executable, "tests/run_isolated_backend.py",
            "tests/backend/test_graph_property_contracts.py", "tests/backend/test_knowledge_graph.py",
            "tests/backend/test_graph_explore_contract.py", "tests/backend/test_extraction_source_authority.py",
            "tests/backend/test_extraction_materialization_contract.py", "tests/backend/test_extraction_review_contract.py",
            "--durations=15", "--disable-warnings"]),
        ("backend-query-revalidation", [sys.executable, "tests/run_isolated_backend.py",
            # 查询准备期间或历史回放时撤权，必须在真正派发和返回旧数据前生效。
            "tests/backend/test_query_source_revalidation.py", "tests/backend/test_query_source_revalidation_boundaries.py",
            "tests/backend/test_query_source_revalidation_dsh.py", "tests/backend/test_query_source_revalidation_replay.py",
            "tests/backend/test_http_query_export_contract.py",
            "--disable-warnings"]),
        # 大版本批次二十三：进程归属与真实硬中断恢复单独验证，沿用每阶段既有预算。
        ("backend-extraction-recovery", [sys.executable, "tests/run_isolated_backend.py",
            "tests/backend/test_extraction_recovery.py", "tests/backend/test_extraction_recovery_process.py",
            "--durations=15", "--disable-warnings"]),
        # 大版本批次十七：库存身份、单位及历史导出单独计时，保留每阶段既有预算。
        ("backend-inventory-contracts", [sys.executable, "tests/run_isolated_backend.py",
            "tests/backend/test_inventory_semantic_counts.py", "tests/backend/test_inventory_query.py",
            "tests/backend/test_inventory_evidence.py", "tests/backend/test_business_data_regression_0928.py",
            "tests/backend/test_inventory_plugin_contract.py", "--disable-warnings"])]
    if not args.skip_build:
        phases.append(("frontend-build", [npm, "--prefix", "apps/analytics", "run", "build"]))
    if os.name == "nt":
        # Windows 标准库使用回环配对；原生 socketpair 平台没有这条 fallback 路径。
        phases.append(("python-socketpair-guard", [sys.executable, "tests/dsh/test_python_socketpair_guard.py"]))
        # 保存器专项中的故障次数断言针对 Windows；不冒充其他平台验证。
        phases.append(("browser-network-evidence", [node, "--test", "tests/ui/network_report_writer.test.cjs"]))
    phases.extend([
        ("browser-port-allocation", [sys.executable, "tests/dsh/test_browser_port_allocation.py"]),
        ("host-recovery", [node, "--test", "services/dsh_host/tests/recovery-request-window.test.mjs",
                           "services/dsh_host/tests/lifecycle-recovery.test.mjs",
                           "services/dsh_host/tests/event-order.test.mjs", "services/dsh_host/tests/projection.test.mjs",
                           "services/dsh_host/tests/business-tool-receipt.test.mjs",
                           "services/dsh_host/tests/durable-receipt.test.mjs", "services/dsh_host/tests/worker.test.mjs"]),
        # 真实stdio丢包和文件库冷重开单独计时；相邻Node合同已在上阶段执行，不重复包装。
        ("backend-receipt-host", [sys.executable, "tests/run_isolated_backend.py",
            "tests/backend/test_durable_receipt_host.py::test_real_host_worker_recovers_lost_reply_and_cold_receipt",
            "--disable-warnings"]),
        # 冷重启故障注入独立计时和落日志，不混入普通业务合同阶段的预算。
        ("host-completion", [sys.executable, "tests/run_isolated_backend.py",
                             "tests/backend/test_dsh_completion_host.py", "--disable-warnings"]),
        ("chart-units", [node, "--test", "tests/ui/snapshot_chart_option.test.js", "tests/ui/saved_dashboard_series.test.js",
                         "tests/ui/chat_dashboard_snapshot.test.js", "tests/ui/chart_precision.test.cjs"]),
        # 独立解读实际下载的 Office 文件，不能让产品自己的页数回执充当验收依据。
        ("compound-file-inspector", [sys.executable, "tests/run_isolated_backend.py",
                                     "tests/offline/test_compound_file_inspector.py", "--disable-warnings"]),
        ("compound-browser-contract", [node, "--test", "tests/ui/compound_browser_contract.test.cjs"]),
        ("chart-precision-browser", [node, "tests/ui/chart_precision_browser.cjs"]),
        ("numeric-display", [node, "--test", "tests/ui/numeric_statistics_display.test.cjs"]),
        ("chat-state", [node, "tests/ui/dsh_chat_session.test.js"]),
        ("stream-recovery", [node, "tests/ui/chat_stream_recovery.test.js"]),
        ("stream-scroll", [node, "tests/ui/chat_stream_scroll.js"]),
        ("dashboard-matrix", [node, "tests/ui/dashboard_delivery_matrix.js", str(output / "dashboard-matrix")]),
        ("native-worker", [sys.executable, "tests/dsh/real_platform_smoke.py",
                           "--output", str(output / "native-worker.json")]),
    ])
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "scope": "合成数据、临时SQLite、本机模型桩；不连接业务库或真实模型",
              "evidence_directory": str(output), "phases": []}

    def save_report():
        """完整报告留在本轮目录，兼容入口仅指向最近一次结果。"""
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        (output / "manifest.json").write_text(encoded, encoding="utf-8")
        (evidence_root / "last-run.json").write_text(encoded, encoding="utf-8")

    save_report()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "PYTHONWARNINGS": "ignore::DeprecationWarning", "TEST5_E2E_PYTHON": sys.executable}
    for name, command in phases:
        print(f"开始 {name}", flush=True)
        start = time.monotonic()
        log_path = output / f"{name}.log"
        with log_path.open("w", encoding="utf-8") as log:
            # 每阶段有上限；失败立即停止，不跳过失败项或悄悄连接真实环境重试。
            try:
                completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           timeout=600, check=False)
                code = completed.returncode
            except subprocess.TimeoutExpired:
                code = 124
        phase = {"name": name, "exit_code": code, "seconds": round(time.monotonic() - start, 2),
                 "command": command, "log": str(log_path)}
        report["phases"].append(phase)
        report["status"] = "failed" if code else "running"
        save_report()
        print(f"{'失败' if code else '通过'} {name}：{phase['seconds']} 秒", flush=True)
        if code:
            print(log_path.read_text(encoding="utf-8", errors="replace")[-6000:], flush=True)
            return code
    report.update(status="passed", completed_at=datetime.now(timezone.utc).isoformat())
    save_report()
    print(f"全部 {len(phases)} 个阶段通过：{output / 'manifest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
