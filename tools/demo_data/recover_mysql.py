"""人工复核原连接已停止后，核验真实已提交批次并显式续接；默认只出计划。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.demo_data.import_mysql import Importer, ImportFailure, qualified, quote, utc_now
from tools.demo_data.package import BATCH_SIZE, META_COLUMNS, PACKAGE_NAME, Plan, batches, build_plan, digest_json


def _load(path: Path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def recovery_plan(plan: Plan, source_plan_file: Path, original_receipt_file: Path) -> tuple[dict, dict]:
    """本地只读预检；候选预算不构成授权，也不建立数据库连接。"""
    frozen, _ = _load(source_plan_file)
    public = plan.public()
    if frozen != public:
        raise ImportFailure("source_plan_changed", "原计划或来源文件已变化，不能续接")
    original, receipt_sha = _load(original_receipt_file)
    connection = original.get("connection_id")
    if (original.get("kind") != "ai_agent_company-demo-import-receipt-v1"
            or original.get("status") not in {"failed", "running"}
            or original.get("plan_sha256") != public["plan_sha256"]
            or type(connection) is not int or connection <= 0 or original.get("connections") != 1):
        raise ImportFailure("original_receipt_invalid", "原收据不支持受审恢复")
    allowed = {asset.import_id: asset for asset in plan.assets}
    seen = set()
    for state in original.get("assets", []):
        identifier = state.get("import_id")
        asset = allowed.get(identifier)
        if (asset is None or identifier in seen or state.get("source_sha256") != asset.source_sha256
                or state.get("database") != asset.database or state.get("table") != asset.table
                or state.get("expected_rows") != asset.row_count
                or type(state.get("committed_rows")) is not int
                or not 0 <= state["committed_rows"] <= asset.row_count):
            raise ImportFailure("original_asset_invalid", "原收据的来源或数量不一致")
        seen.add(identifier)
    body = {"kind": "ai_agent_company-demo-recovery-plan-v1",
            "original_plan_sha256": public["plan_sha256"], "original_receipt_sha256": receipt_sha,
            "original_connection_id": connection, "connection_budget": 1,
            "statement_timeout_seconds": 5, "total_timeout_seconds": 1800,
            "batch_size": BATCH_SIZE, "source_kind": "synthetic",
            "sql_statement_budget": public["sql_statement_budget"] + 4 * public["batches"] + 20 * len(plan.assets) + 100,
            "commit_budget": public["commit_budget"] + len(plan.assets) * 3,
            "maximum_readback_rows": public["rows"], "maximum_readback_batches": public["batches"],
            "requires_reviewed_stop": True, "writes_existing_rows": False,
            "assets": public["assets"]}
    return {**body, "recovery_plan_sha256": digest_json(body)}, original


def reviewed_stop(review_file: Path, candidate: dict) -> tuple[dict, str]:
    """停止及恢复授权必须绑定不可变原收据，不接受泛泛的已检查声明。"""
    review, sha = _load(review_file)
    required = {"kind": "ai_agent_company-demo-import-stop-review-v1",
                "original_receipt_sha256": candidate["original_receipt_sha256"],
                "plan_sha256": candidate["original_plan_sha256"],
                "connection_id": candidate["original_connection_id"],
                "connection_stopped": True, "importer_stopped": True, "resume_authorized": True}
    if any(review.get(key) != value or (isinstance(value, bool) and type(review.get(key)) is not bool) for key, value in required.items()):
        raise ImportFailure("review_invalid", "停止证明、原收据或恢复授权未匹配")
    if review.get("recovery_plan_sha256", candidate["recovery_plan_sha256"]) != candidate["recovery_plan_sha256"]:
        raise ImportFailure("review_plan_changed", "恢复计划与复核记录不一致")
    try:
        stamp = datetime.fromisoformat(review["checked_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if stamp.tzinfo is None or not -60 <= age <= 3600 or not str(review.get("reviewed_by", "")).strip():
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportFailure("review_expired", "停止复核时间无效或已过期") from exc
    return review, sha


def canonical_rows(records: list[dict], expected_rows: list[tuple], names: tuple[str, ...]) -> list[tuple]:
    """只还原驱动返回的类型，不舍入原浮点值，不把 NULL 或空串互换。"""
    if len(records) != len(expected_rows):
        raise ImportFailure("recovery_data_mismatch", "已提交批次的真实行数不一致")
    result = []
    for actual, expected in zip(records, expected_rows):
        values = []
        for name, reference in zip(names, expected):
            value = actual.get(name)
            try:
                if reference is None:
                    if value is not None:
                        raise ValueError()
                elif isinstance(reference, bool):
                    if value not in (0, 1, False, True):
                        raise ValueError()
                    value = bool(value)
                elif isinstance(reference, int):
                    precise = Decimal(str(value))
                    if not precise.is_finite() or precise != precise.to_integral_value():
                        raise ValueError()
                    value = int(precise)
                elif isinstance(reference, float):
                    value = float(value)
                    if not math.isfinite(value):
                        raise ValueError()
                elif isinstance(reference, Decimal):
                    value = Decimal(str(value))
                    if not value.is_finite():
                        raise ValueError()
                elif not isinstance(value, str):
                    raise ValueError()
            except (ValueError, TypeError, ArithmeticError) as exc:
                raise ImportFailure("recovery_data_mismatch", "已提交批次的字段类型或缺失值已变化") from exc
            values.append(value)
        result.append(tuple(values))
    return result


class RecoveryImporter(Importer):
    """复用原连接、预算和事务边界；先验证所有来源，再续接已确认断点。"""

    def __init__(self, plan, *, original_receipt_file, review_file, candidate, original, **kwargs):
        super().__init__(plan, **kwargs)
        self.original_receipt_file, self.review_file = Path(original_receipt_file), Path(review_file)
        self.candidate, self.original = candidate, original
        _, self.review_sha = reviewed_stop(self.review_file, candidate)
        self.budgets = {**self.budgets, **{key: candidate[key] for key in
            ("total_timeout_seconds", "sql_statement_budget", "commit_budget")}}
        self.original_states = {row["import_id"]: row for row in original.get("assets", [])}
        self.incomplete_seen = False
        self.receipt["recovery"] = {"original_receipt_sha256": candidate["original_receipt_sha256"],
            "review_sha256": self.review_sha, "recovery_plan_sha256": candidate["recovery_plan_sha256"],
            "original_connection_id": candidate["original_connection_id"], "stop_rechecked": False}
        self.receipt["recovery_budget"] = 1800
        self.receipt["verified_sources"] = []

    def _connect(self):
        if _load(self.original_receipt_file)[1] != self.candidate["original_receipt_sha256"]:
            raise ImportFailure("original_receipt_changed", "原导入收据仍在变化，不能恢复")
        if reviewed_stop(self.review_file, self.candidate)[1] != self.review_sha:
            raise ImportFailure("review_changed", "停止复核记录已变化")
        super()._connect()
        old = self.original.get("instance", {})
        current = self.receipt["instance"]
        if not old or any(old.get(key) != current.get(key) for key in ("server_uuid", "data_directory", "port")):
            raise ImportFailure("recovery_instance_changed", "原导入与当前数据库实例不一致")
        records = self.query("SELECT ID FROM information_schema.PROCESSLIST WHERE ID=%s", (self.candidate["original_connection_id"],))
        if records:
            raise ImportFailure("original_connection_running", "原连接仍存在，已拒绝恢复")
        self.receipt["recovery"]["stop_rechecked"] = True

    def ensure_table(self, database, table, columns, comment, *, primary, extras=()):
        records = self.query("SELECT TABLE_COMMENT,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (database, table))
        if not records:
            raise ImportFailure("recovery_table_missing", "原导入表或账本缺失，不能恢复")
        return super().ensure_table(database, table, columns, comment, primary=primary, extras=extras)

    def _batch_records(self, asset):
        maximum = (asset.row_count + BATCH_SIZE - 1) // BATCH_SIZE
        records = self.query(f"SELECT * FROM {qualified(asset.database, 'aic_demo_batches')} WHERE import_id=%s ORDER BY batch_no LIMIT %s", (asset.import_id, maximum + 1))
        if len(records) > maximum or [int(row["batch_no"]) for row in records] != list(range(1, len(records) + 1)):
            raise ImportFailure("recovery_batches_invalid", "已提交批次缺号、重复或超出来源范围")
        return records

    def _existing(self, asset):
        self.receipt["phase"] = "source_verification"
        ledger = self.query(f"SELECT * FROM {qualified(asset.database, 'aic_demo_imports')} WHERE import_id=%s", (asset.import_id,))
        actual_count = self._count(asset)
        committed_batches = self._batch_records(asset)
        state = {"import_id": asset.import_id, "source": asset.source,
                 "committed_rows": 0, "batch_count": 0, "status": "new"}
        if not ledger:
            if actual_count or committed_batches:
                raise ImportFailure("recovery_untracked_rows", "发现缺少来源账本的数据，不能恢复")
            self.incomplete_seen = True
            self.receipt["verified_sources"].append(state)
            self.save_receipt()
            return state
        if self.incomplete_seen or len(ledger) != 1:
            raise ImportFailure("recovery_source_order_invalid", "来源提交顺序与原串行导入不一致")
        row = ledger[0]
        if (row.get("source_sha256") != asset.source_sha256 or row.get("source_path") != asset.source
                or row.get("table_name") != asset.table or int(row.get("expected_rows", -1)) != asset.row_count
                or row.get("status") not in {"running", "failed", "succeeded"}):
            raise ImportFailure("recovery_ledger_mismatch", "来源账本与冻结计划不一致")
        count = int(row.get("committed_rows", -1))
        prior = self.original_states.get(asset.import_id, {}).get("committed_rows", 0)
        if (not 0 <= count <= asset.row_count or actual_count != count
                or sum(int(item["row_count"]) for item in committed_batches) != count
                or not 0 <= count - prior <= BATCH_SIZE):
            raise ImportFailure("recovery_count_mismatch", "真实行、批次、来源账本与原收据的提交点不一致")
        names = (*META_COLUMNS, *(column.name for column in asset.columns))
        verified = 0
        for number, (source_rows, source_sha) in enumerate(batches(self.plan, asset), start=1):
            if number > len(committed_batches):
                continue
            batch = committed_batches[number - 1]
            if (int(batch["first_source_row"]) != source_rows[0][1]
                    or int(batch["last_source_row"]) != source_rows[-1][1]
                    or int(batch["row_count"]) != len(source_rows)
                    or batch["content_sha256"] != source_sha):
                raise ImportFailure("recovery_batch_hash_mismatch", "已提交批次的范围或来源哈希不一致")
            selected = ",".join(quote(name) for name in names)
            real = self.query(f"SELECT {selected} FROM {qualified(asset.database, asset.table)} WHERE `__aic_import_id`=%s AND `__aic_source_row` BETWEEN %s AND %s ORDER BY `__aic_source_row` LIMIT %s",
                (asset.import_id, source_rows[0][1], source_rows[-1][1], BATCH_SIZE + 1))
            if digest_json(canonical_rows(real, source_rows, names)) != source_sha:
                raise ImportFailure("recovery_data_hash_mismatch", "真实已提交数据与来源批次哈希不一致")
            verified += len(source_rows)
        if verified != count or (row["status"] == "succeeded" and count != asset.row_count):
            raise ImportFailure("recovery_checkpoint_invalid", "提交点不在完整批次边界或成功状态不完整")
        state.update(committed_rows=count, batch_count=len(committed_batches), status=row["status"])
        if row["status"] != "succeeded":
            self.incomplete_seen = True
        self.receipt["verified_sources"].append(state)
        self.save_receipt()
        return state

    def _locked_checkpoint(self, asset, count, status):
        rows = self.query(f"SELECT * FROM {qualified(asset.database, 'aic_demo_imports')} WHERE import_id=%s FOR UPDATE", (asset.import_id,))
        if (len(rows) != 1 or rows[0].get("status") != status
                or int(rows[0].get("committed_rows", -1)) != count
                or rows[0].get("source_sha256") != asset.source_sha256):
            raise ImportFailure("recovery_checkpoint_changed", "复核后的提交点又发生变化，已停止恢复")

    def _import(self, asset, *, already_succeeded):
        checkpoint = already_succeeded
        if checkpoint["status"] == "new":
            return super()._import(asset)
        state = {"import_id": asset.import_id, "database": asset.database, "table": asset.table,
                 "source": asset.source, "source_sha256": asset.source_sha256,
                 "expected_rows": asset.row_count, "committed_rows": checkpoint["committed_rows"],
                 "status": "already_succeeded" if checkpoint["status"] == "succeeded" else "resuming"}
        self.receipt["assets"].append(state)
        if checkpoint["status"] == "succeeded":
            self.save_receipt()
            return
        self.receipt["phase"] = "resume_batch"
        total = checkpoint["committed_rows"]
        ledger = qualified(asset.database, "aic_demo_imports")
        self._locked_checkpoint(asset, total, checkpoint["status"])
        if checkpoint["status"] == "failed":
            changed = self.cursor.execute(f"UPDATE {ledger} SET status='running' WHERE import_id=%s AND status='failed' AND committed_rows=%s", (asset.import_id, total))
            if changed != 1:
                raise ImportFailure("recovery_checkpoint_changed", "来源状态已变化")
        self.commit()
        names = (*META_COLUMNS, *(column.name for column in asset.columns))
        for number, (rows, content_sha) in enumerate(batches(self.plan, asset), start=1):
            if number <= checkpoint["batch_count"]:
                continue
            self._locked_checkpoint(asset, total, "running")
            placeholders = "(" + ",".join(["%s"] * len(names)) + ")"
            sql = f"INSERT INTO {qualified(asset.database, asset.table)} ({','.join(quote(name) for name in names)}) VALUES " + ",".join([placeholders] * len(rows))
            self.cursor.execute(sql, tuple(value for row in rows for value in row))
            self.cursor.execute(f"INSERT INTO {qualified(asset.database, 'aic_demo_batches')} (import_id,batch_no,first_source_row,last_source_row,row_count,content_sha256,committed_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (asset.import_id, number, rows[0][1], rows[-1][1], len(rows), content_sha, utc_now()))
            changed = self.cursor.execute(f"UPDATE {ledger} SET committed_rows=%s WHERE import_id=%s AND status='running' AND committed_rows=%s", (total + len(rows), asset.import_id, total))
            if changed != 1:
                raise ImportFailure("recovery_checkpoint_changed", "来源提交点已变化")
            total += len(rows)
            self.commit()
            state["committed_rows"] = total
            self.receipt["rows_committed_this_run"] += len(rows)
            self.save_receipt()
        if total != asset.row_count or self._count(asset) != asset.row_count:
            raise ImportFailure("recovery_final_count_mismatch", "恢复后的行数与来源不一致")
        self._locked_checkpoint(asset, total, "running")
        changed = self.cursor.execute(f"UPDATE {ledger} SET status='succeeded',finished_at=%s WHERE import_id=%s AND status='running'", (utc_now(), asset.import_id))
        if changed != 1:
            raise ImportFailure("recovery_checkpoint_changed", "来源终态已变化")
        self.commit()
        state["status"] = "succeeded"
        self.save_receipt()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, default=REPO / PACKAGE_NAME)
    parser.add_argument("--source-plan-file", type=Path, required=True)
    parser.add_argument("--original-receipt", type=Path, required=True)
    parser.add_argument("--plan-output", type=Path)
    parser.add_argument("--recovery-plan-file", type=Path)
    parser.add_argument("--review-file", type=Path)
    parser.add_argument("--resume-after-reviewed-stop", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--username", default=os.environ.get("AIC_DEMO_MYSQL_USER", ""))
    parser.add_argument("--password-env", default="AIC_DEMO_MYSQL_PASSWORD")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        plan = build_plan(args.package_root)
        candidate, original = recovery_plan(plan, args.source_plan_file, args.original_receipt)
        if not args.execute:
            if args.plan_output:
                args.plan_output.parent.mkdir(parents=True, exist_ok=True)
                with args.plan_output.open("x", encoding="utf-8") as stream:
                    stream.write(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(candidate, ensure_ascii=False))
            return 0
        if not (args.resume_after_reviewed_stop and args.recovery_plan_file and args.review_file and args.proof and args.username):
            raise ImportFailure("reviewed_resume_required", "恢复必须显式指定受审停止、计划、证明与独立收据")
        if _load(args.recovery_plan_file)[0] != candidate:
            raise ImportFailure("recovery_plan_changed", "恢复计划或原收据已变化")
        reviewed_stop(args.review_file, candidate)
        password = os.environ.get(args.password_env) or getpass.getpass("本机MySQL密码：")
        receipt = args.receipt or REPO / ".local/mysql-demo" / ("recovery-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4) + ".json")
        result = RecoveryImporter(plan, original_receipt_file=args.original_receipt, review_file=args.review_file,
            candidate=candidate, original=original, proof_file=args.proof, username=args.username,
            password=password, receipt_file=receipt).run()
        password = ""
        print(json.dumps({"status": result["status"], "receipt": str(receipt),
            "rows_committed_this_run": result["rows_committed_this_run"], "connections": result["connections"],
            "statements": result["statements"], "error": result.get("error"),
            "requires_query_stop_check": result["requires_query_stop_check"]}, ensure_ascii=False))
        return 0 if result["status"] == "succeeded" else 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": {"code": exc.code if isinstance(exc, ImportFailure) else "recovery_invalid",
            "message": str(exc) if isinstance(exc, ImportFailure) else "恢复计划或本地材料核查失败"}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
