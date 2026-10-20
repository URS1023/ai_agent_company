"""食品产品三维质检合成样本：固定来源、公开示意图及现有导入账本。"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import getpass
import json
import os
from pathlib import Path
import secrets
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "services/platform") not in sys.path:
    sys.path.insert(0, str(REPO / "services/platform"))

from tools.demo_data.import_mysql import (
    BATCH_COLUMNS, DATA_META, IMPORT_COLUMNS, Importer, ImportFailure, OWNER,
)
from tools.demo_data.package import Asset, Column, META_COLUMNS, PackageError, Plan, file_sha256


CSV_PATH = REPO / "fixtures/食品产品质检.csv"
IMAGE_ROOT = REPO / "apps/web/public"
DATABASE = "szh_new_food"
TABLE = "aic_demo_product_quality"
SOURCE_KIND = "synthetic_demo_v1"
STANDARD_CODE = "DEMO-FOOD-QC-V1"
IMAGE_NAMES = frozenset({"normal", "seam-open", "wrinkle", "uncertain", "uninspected"})
FIELDS = (
    "设备编号", "sample_id", "product_code", "product_name", "batch_no", "observed_at",
    "net_weight_g", "protein_pct", "moisture_pct", "appearance_defects_json",
    "appearance_image_ref", "data_origin",
)
NUMERIC_FIELDS = ("net_weight_g", "protein_pct", "moisture_pct")


def template_input() -> dict:
    """返回平台 TemplateInput；演示阈值不冒充国家或企业放行标准。"""
    return {"name": "食品产品检验（演示）", "code": STANDARD_CODE,
            "description": "合成食品样本与包装示意图；仅验证数值、成分与外观判定流程。",
            "target_type": "product", "metrics": [
                {"id": "net_weight", "name": "净重", "category": "numeric", "unit": "g", "standard_min": "98", "standard_max": "102"},
                {"id": "protein", "name": "蛋白质", "category": "component", "component_name": "蛋白质", "unit": "%", "standard_min": "8", "standard_max": "12"},
                {"id": "moisture", "name": "水分", "category": "component", "component_name": "水分", "unit": "%", "standard_max": "5"},
                {"id": "appearance", "name": "包装外观", "category": "appearance", "unit": "", "minimum_confidence": "0.8", "blocking_severities": ["moderate", "severe"]},
            ]}


def inspection_import(query_id: str, revision: int) -> dict:
    """由服务端受控重跑查询，四项观测按同一稳定样本编号归属。"""
    return {"query_id": query_id, "revision": revision, "sample_field": "sample_id", "mappings": [
        {"metric_id": "net_weight", "value_field": "net_weight_g", "unit": "g"},
        {"metric_id": "protein", "value_field": "protein_pct", "unit": "%"},
        {"metric_id": "moisture", "value_field": "moisture_pct", "unit": "%"},
        {"metric_id": "appearance", "image_ref_field": "appearance_image_ref", "defects_field": "appearance_defects_json"},
    ]}


def query_input(datasource_id: str, import_id: str) -> dict:
    """按部署者核对为成功的来源编号取数，平台继续检查当前行列权限。"""
    fields = ",".join("`" + name + "`" for name in FIELDS)
    return {"name": "食品三维质检样本（演示）", "datasource_id": datasource_id,
            "sql": f"SELECT {fields} FROM `{TABLE}` WHERE `__aic_import_id`=:import_id ORDER BY `sample_id`",
            "parameters": {"import_id": import_id}, "max_rows": 100}


def fixture_rows(path: Path = CSV_PATH) -> list[dict]:
    """空字符串保持未测，JSON null 表示未提交外观结果；两者不补零。"""
    from agent_platform.modules.manufacturing.schemas import Defect

    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise PackageError("食品质检字段与固定映射不一致")
        rows = list(reader)
    if len(rows) != 16 or len({row.get("sample_id") for row in rows}) != len(rows):
        raise PackageError("食品质检样本数量或编号不符合本次固定包")
    for row in rows:
        if (None in row or any(value is None for value in row.values())
                or row["设备编号"] != "SP-JZ-001" or row["data_origin"] != SOURCE_KIND
                or not row["sample_id"].startswith("DEMO-FD-QC-")
                or not row["product_code"].startswith("DEMO-") or "演示" not in row["product_name"]):
            raise PackageError("食品样本缺少明确的合成来源或归属")
        try:
            stamp = datetime.fromisoformat(row["observed_at"])
            if stamp.tzinfo is None:
                raise ValueError()
            for field in NUMERIC_FIELDS:
                if row[field] and not Decimal(row[field]).is_finite():
                    raise ValueError()
            defects = json.loads(row["appearance_defects_json"])
            if defects is not None:
                if not isinstance(defects, list):
                    raise ValueError()
                for defect in defects:
                    Defect.model_validate(defect)
        except (ValueError, InvalidOperation, TypeError) as exc:
            raise PackageError("食品样本的数值、时间或外观字段无效") from exc
        reference = row["appearance_image_ref"]
        if reference:
            valid = {f"/demo/quality/{name}.svg" for name in IMAGE_NAMES}
            if reference not in valid or not (IMAGE_ROOT / reference.lstrip("/")).is_file():
                raise PackageError("食品外观示意图尚未落盘或路径不受支持")
    return rows


def build_quality_plan(path: Path = CSV_PATH) -> Plan:
    """食品值按原文保留，空值留空，交由现有 Decimal 判定接口解释。"""
    path = path.resolve(strict=True)
    rows = fixture_rows(path)
    bounded = {"设备编号": 80, "sample_id": 160, "product_code": 80,
               "product_name": 160, "batch_no": 100, "observed_at": 48, "data_origin": 40}
    columns = tuple(Column(name, f"VARCHAR({bounded[name]})" if name in bounded else "LONGTEXT", "synthetic-source-text") for name in FIELDS)
    asset = Asset(path.name, file_sha256(path), path.stat().st_size, "csv", DATABASE, TABLE, len(rows), columns)
    return Plan(path.parent, (asset,))


class QualityImporter(Importer):
    """复用批次事务及连接保护，仅在食品库登记本轮的新质检表。"""

    def _tables(self):
        self.receipt["phase"] = "table_ownership"
        if len(self.plan.assets) != 1 or (self.plan.assets[0].database, self.plan.assets[0].table) != (DATABASE, TABLE):
            raise ImportFailure("quality_target_invalid", "食品样本只能导入指定独立新表")
        self.ensure_table(DATABASE, "aic_demo_imports", IMPORT_COLUMNS,
            f"{OWNER};kind=imports;schema=1", primary=("import_id",))
        self.ensure_table(DATABASE, "aic_demo_batches", BATCH_COLUMNS,
            f"{OWNER};kind=batches;schema=1", primary=("import_id", "batch_no"))
        asset = self.plan.assets[0]
        self.ensure_table(DATABASE, TABLE, (*DATA_META, *asset.columns), asset.comment,
            primary=META_COLUMNS[:2], extras=("KEY `ix_quality_sample` (`sample_id`)",))
        self.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-file", type=Path, default=CSV_PATH)
    parser.add_argument("--plan-output", type=Path)
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--username", default=os.environ.get("AIC_DEMO_MYSQL_USER", ""))
    parser.add_argument("--password-env", default="AIC_DEMO_MYSQL_PASSWORD")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        plan = build_quality_plan(args.csv_file)
        public = plan.public()
        if not args.execute:
            if args.plan_output:
                args.plan_output.parent.mkdir(parents=True, exist_ok=True)
                with args.plan_output.open("x", encoding="utf-8") as stream:
                    stream.write(json.dumps(public, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(public, ensure_ascii=False))
            return 0
        if not args.plan_file or not args.proof or not args.username:
            raise ImportFailure("execution_arguments_missing", "执行需要已审查计划、实例证明和用户名")
        if json.loads(args.plan_file.read_text(encoding="utf-8")) != public:
            raise ImportFailure("plan_changed", "食品样本或计划已变化，请重新核对")
        password = os.environ.get(args.password_env) or getpass.getpass("本机MySQL密码：")
        receipt_path = args.receipt or REPO / ".local/mysql-demo" / ("quality-import-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4) + ".json")
        result = QualityImporter(plan, proof_file=args.proof, username=args.username,
            password=password, receipt_file=receipt_path).run()
        password = ""
        print(json.dumps({"status": result["status"], "receipt": str(receipt_path),
            "connections": result["connections"], "statements": result["statements"],
            "rows_committed_this_run": result["rows_committed_this_run"], "error": result.get("error"),
            "requires_query_stop_check": result["requires_query_stop_check"]}, ensure_ascii=False))
        return 0 if result["status"] == "succeeded" else 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": {"code": exc.code if isinstance(exc, ImportFailure) else "quality_fixture_invalid",
            "message": str(exc) if isinstance(exc, (ImportFailure, PackageError)) else "食品样本核查失败"}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
