"""读取随项目保存的合成设备数据，生成可核对的全量导入计划。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Iterator


PACKAGE_NAME = "设备预测维护数据包"
BATCH_SIZE = 1000
EXPECTED_DEVICE_ROWS = 259200
DATABASES = ("szh_new", "szh_new_food")
INDUSTRIES = {"SP-JZ": "food", "HG-LXB": "chemical", "FZ-PQZJ": "textile"}
DEVICE_IDS = tuple(f"{prefix}-{number:03}" for prefix in INDUSTRIES for number in range(1, 4))
META_COLUMNS = ("__aic_import_id", "__aic_source_row", "__aic_source_kind")
OWNER = "ai_agent_company/demo-data/v1"
SMALL_FILES = (
    ("设备台账.csv", "assets"),
    ("运行记录/启停与状态事件.csv", "events"),
    ("运行记录/开关机会话.csv", "sessions"),
    ("运行记录/维修保养工单.jsonl", "work_orders"),
    ("运行记录/巡检记录.jsonl", "inspections"),
    ("运行记录/历史经验.jsonl", "experience"),
)


class PackageError(RuntimeError):
    """数据包内容不符合已核对的交付契约。"""


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    source_type: str


@dataclass(frozen=True)
class Asset:
    """一个源文件在一个目标库中的分区；原始行号不会因分区改变。"""

    source: str
    source_sha256: str
    size_bytes: int
    format: str
    database: str
    table: str
    row_count: int
    columns: tuple[Column, ...]
    device_id: str | None = None

    @property
    def import_id(self) -> str:
        return digest_json({"source": self.source, "sha256": self.source_sha256,
                            "database": self.database, "table": self.table})

    @property
    def schema_hash(self) -> str:
        return digest_json([asdict(column) for column in self.columns])

    @property
    def comment(self) -> str:
        return f"{OWNER};kind=dataset;schema={self.schema_hash}"

    def public(self) -> dict:
        return {**asdict(self), "columns": [asdict(column) for column in self.columns],
                "import_id": self.import_id, "schema_hash": self.schema_hash,
                "batch_count": math.ceil(self.row_count / BATCH_SIZE)}


@dataclass(frozen=True)
class Plan:
    package_root: Path
    assets: tuple[Asset, ...]

    def public(self) -> dict:
        """计划哈希只覆盖来源和预算，不混入机器绝对路径或生成时间。"""
        batches = sum(math.ceil(asset.row_count / BATCH_SIZE) for asset in self.assets)
        tables = sorted({(asset.database, asset.table) for asset in self.assets})
        body = {"kind": "ai_agent_company-demo-import-plan-v1", "source_kind": "synthetic",
                "batch_size": BATCH_SIZE, "connection_budget": 1,
                "statement_timeout_seconds": 5, "total_timeout_seconds": 900,
                "sql_statement_budget": 180 + len(self.assets) * 24 + batches * 3,
                "read_plan_max_rows": 500000, "read_plan_requires_index": True,
                "commit_budget": 20 + len(self.assets) * 4 + batches,
                "rows": sum(asset.row_count for asset in self.assets), "batches": batches,
                "tables": [{"database": db, "table": table} for db, table in tables],
                "assets": [asset.public() for asset in self.assets]}
        return {**body, "plan_sha256": digest_json(body)}


def digest_json(value) -> str:
    def scalar(item):
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        raise TypeError("不支持的摘要值类型")
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=scalar).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def source_path(root: Path, relative: str) -> Path:
    """仅接受包内固定资产，不沿链接读取包外文件。"""
    root = root.resolve(strict=True)
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise PackageError("来源文件不在本次设备数据包内")
    return path


def database_for_device(device_id: str) -> str:
    if device_id not in DEVICE_IDS:
        raise PackageError("发现未登记的设备编号")
    return "szh_new_food" if device_id.startswith("SP-JZ-") else "szh_new"


def _identifier(name: str) -> str:
    if not name or len(name) > 64 or "\0" in name or name in META_COLUMNS:
        raise PackageError("源字段名无效或占用了导入器保留字段")
    return name


def _text_type(name: str) -> str:
    if name == "设备编号":
        return "VARCHAR(80)"
    if name in {"采集时间", "入库时间"}:
        return "VARCHAR(48)"
    return "LONGTEXT"


def _arrow_column(field) -> Column:
    """保持原浮点类型值；带时区时间按原 ISO 文本保存。"""
    import pyarrow as pa

    name, kind = _identifier(field.name), field.type
    source_type = str(kind)
    if pa.types.is_dictionary(kind):
        kind = kind.value_type
    if pa.types.is_floating(kind):
        sql_type = "DOUBLE"
    elif pa.types.is_unsigned_integer(kind):
        sql_type = "DECIMAL(20,0)"
    elif pa.types.is_integer(kind):
        sql_type = "BIGINT"
    elif pa.types.is_boolean(kind):
        sql_type = "TINYINT(1)"
    elif pa.types.is_decimal(kind) and kind.precision <= 65 and kind.scale <= 30:
        sql_type = f"DECIMAL({kind.precision},{kind.scale})"
    elif pa.types.is_string(kind) or pa.types.is_large_string(kind) or pa.types.is_temporal(kind):
        sql_type = _text_type(name)
    else:
        raise PackageError(f"尚未支持的源字段类型：{name}")
    return Column(name, sql_type, source_type)


def _small_rows(path: Path) -> tuple[list[str], list[dict]]:
    if path.suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = list(reader.fieldnames or [])
            rows = list(reader)
            if any(None in row or any(value is None for value in row.values()) for row in rows):
                raise PackageError("CSV行列数与表头不符")
    else:
        rows = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise PackageError("JSONL记录必须为对象")
                rows.append(row)
        columns = list(dict.fromkeys(key for row in rows for key in row))
    if "设备编号" not in columns or not rows or len(columns) != len({name.casefold() for name in columns}):
        raise PackageError("小文件缺少唯一字段表头或设备编号")
    for name in columns:
        _identifier(name)
    for row in rows:
        database_for_device(str(row.get("设备编号", "")))
    return columns, rows


def build_plan(package_root: Path) -> Plan:
    """预读每个 Parquet 页脚并校验清单，规划阶段没有数据库连接。"""
    import pyarrow.parquet as pq

    package_root = package_root.resolve(strict=True)
    manifest = json.loads(source_path(package_root, "数据规模.json").read_text(encoding="utf-8-sig"))
    if manifest.get("设备数") != 9 or manifest.get("监测总行数") != 9 * EXPECTED_DEVICE_ROWS:
        raise PackageError("数据规模清单与本次九台设备范围不符")
    assets = []
    for device in DEVICE_IDS:
        relative = f"监测数据/{device}/监测.parquet"
        path = source_path(package_root, relative)
        parquet = pq.ParquetFile(path)
        try:
            columns = tuple(_arrow_column(field) for field in parquet.schema_arrow)
            expected_columns = 57 if device.startswith("FZ-") else 56
            if parquet.metadata.num_rows != EXPECTED_DEVICE_ROWS or len(columns) != expected_columns:
                raise PackageError(f"设备 {device} 的实际行列数与已核对规模不符")
            if len({column.name.casefold() for column in columns}) != len(columns):
                raise PackageError("Parquet字段重复")
        finally:
            parquet.close()
        industry = next(value for prefix, value in INDUSTRIES.items() if device.startswith(prefix))
        assets.append(Asset(relative, file_sha256(path), path.stat().st_size, "parquet",
                            database_for_device(device), f"aic_demo_{industry}_telemetry",
                            EXPECTED_DEVICE_ROWS, columns, device))
    for relative, kind in SMALL_FILES:
        path = source_path(package_root, relative)
        names, rows = _small_rows(path)
        columns = tuple(Column(name, _text_type(name), "source-text") for name in names)
        source_sha = file_sha256(path)
        for database in DATABASES:
            count = sum(database_for_device(str(row["设备编号"])) == database for row in rows)
            if count:
                assets.append(Asset(relative, source_sha, path.stat().st_size, path.suffix[1:],
                                    database, f"aic_demo_{kind}", count, columns))
    return Plan(package_root, tuple(assets))


def _value(value, column: Column):
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        # Parquet 中 NaN 是缺失值；不以零代替未知观测。
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if column.sql_type.startswith(("LONGTEXT", "VARCHAR")):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)
    return value


def batches(plan: Plan, asset: Asset) -> Iterator[tuple[list[tuple], str]]:
    """逐批读取完整来源，保留文件原始行号；不读取评测答案或注入配置。"""
    path = source_path(plan.package_root, asset.source)
    if path.stat().st_size != asset.size_bytes or file_sha256(path) != asset.source_sha256:
        raise PackageError("来源文件在计划后发生变化")
    committed = 0
    pending = []
    import_id = asset.import_id

    def prepare(number: int, row: dict) -> tuple | None:
        device = str(row.get("设备编号", ""))
        if asset.device_id and device != asset.device_id:
            raise PackageError("设备监测文件包含其他设备数据")
        if database_for_device(device) != asset.database:
            return None
        return (import_id, number, "synthetic", *(_value(row.get(column.name), column) for column in asset.columns))

    if asset.format == "parquet":
        import pyarrow.parquet as pq

        parquet = pq.ParquetFile(path)
        try:
            if parquet.metadata.num_rows != asset.row_count:
                raise PackageError("监测元数据行数已改变")
            number = 0
            for batch in parquet.iter_batches(batch_size=BATCH_SIZE):
                for row in batch.to_pylist():
                    number += 1
                    prepared = prepare(number, row)
                    if prepared is not None:
                        pending.append(prepared)
                if pending:
                    committed += len(pending)
                    yield pending, digest_json(pending)
                    pending = []
        finally:
            parquet.close()
    else:
        _, rows = _small_rows(path)
        for number, row in enumerate(rows, start=1):
            prepared = prepare(number, row)
            if prepared is not None:
                pending.append(prepared)
            if len(pending) == BATCH_SIZE:
                committed += len(pending)
                yield pending, digest_json(pending)
                pending = []
        if pending:
            committed += len(pending)
            yield pending, digest_json(pending)
    if committed != asset.row_count or file_sha256(path) != asset.source_sha256:
        raise PackageError("来源行数或哈希在读取期间发生变化")
