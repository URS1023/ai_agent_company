"""Import user-selected tabular files into a fresh workspace-owned SQLite source."""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from pathlib import Path

from app.common.authorization import require_permission
from app.errors import ValidationError
from app.ids import new_id
from app.modules.analytics.connections import managed_database_path
from app.modules.analytics.schemas import SourceInput
from app.modules.analytics.sources import create_source

MAX_UPLOAD = 20 * 1024 * 1024
MAX_IMPORT_ROWS = 50000


def import_tabular(ctx, *, filename: str, data: bytes, name: str, table_name: str, encoding: str = "utf-8-sig"):
    """把用户主动上传的 CSV/XLSX 导入成新的受管 SQLite 数据源。

    权限：``datasources.create``。参数：``data`` 为已读取的字节（路由层限制 20 MB 后
    再传入，本函数再校验一次大小）；``encoding`` 只允许 utf-8-sig / gb18030。

    安全与写库约束：
      表名走 ``[\\w]{1,100}`` 白名单并禁止 sqlite_/pg_ 前缀，列名与表名一律引号化；
      XLSX 先按 zip 校验解压后总大小（防 zip 炸弹）再用只读模式解析，且 keep_links=False；
      全部列固定声明为 TEXT（保留编号前导零与原始小数，数值运算需显式转换），
      因此导入过程不会因类型推断而改写业务数据。
    抛出：超出大小/行数/列数、结构不可解析、标题重复或为空时抛 ``ValidationError``。
    副作用：新建 SQLite 文件与数据源记录（不会覆盖任何已有文件），并触发一次元数据同步。
    """
    require_permission(ctx, "datasources.create")
    if not re.fullmatch(r"[\w]{1,100}", table_name) or table_name.lower().startswith(("sqlite_", "pg_")):
        raise ValidationError("数据表名只允许中文、英文字母、数字和下划线，不能使用系统表前缀")
    if len(data) > MAX_UPLOAD:
        raise ValidationError("单次导入文件不能超过 20 MB")
    extension = Path(filename).suffix.lower()
    if extension == ".csv":
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError as exc:
            raise ValidationError("CSV 编码不匹配，请选择 UTF-8 或 GB18030") from exc
        try:
            reader = csv.reader(io.StringIO(text))
            rows = []
            for row in reader:
                if len(rows) > MAX_IMPORT_ROWS:
                    raise ValidationError("导入超过 50000 行，请拆分文件或连接数据库")
                rows.append(row)
        except csv.Error as exc:
            raise ValidationError("CSV 结构无法解析") from exc
    elif extension == ".xlsx":
        from openpyxl import load_workbook
        from zipfile import BadZipFile, ZipFile
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                    raise ValidationError("XLSX 解压后超过 100 MB")
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
            try:
                rows = []
                for row in workbook.active.iter_rows(values_only=True):
                    if len(rows) > MAX_IMPORT_ROWS:
                        raise ValidationError("导入超过 50000 行，请拆分文件")
                    rows.append(list(row))
            finally:
                workbook.close()
        except (BadZipFile, KeyError, ValueError) as exc:
            raise ValidationError("XLSX 文件无法解析，请检查文件格式") from exc
    else:
        raise ValidationError("目前支持上传 CSV 或 XLSX；数据库连接请使用连接配置")
    if len(rows) < 2 or not rows[0]:
        raise ValidationError("文件必须包含标题行和至少一行数据")
    if len(rows[0]) > 200:
        raise ValidationError("最多支持 200 列")
    headers = [str(value or "").strip() for value in rows[0]]
    if any(not value or len(value) > 200 or "\0" in value for value in headers) or len(set(headers)) != len(headers):
        raise ValidationError("标题必须非空、互不重复，且不超过 200 字符")
    if any(len(row) != len(headers) for row in rows[1:]):
        raise ValidationError("数据行列数与标题不一致，请修复文件后重试")
    database_filename = new_id() + ".sqlite3"
    path = managed_database_path(ctx.workspace_id, database_filename, must_exist=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    quoted_table = '"' + table_name.replace('"', '""') + '"'
    # TEXT preserves IDs, leading zeros and decimal source values exactly.
    # Numeric conversion is explicit, never silently inferred from identifiers.
    declarations = ",".join('"' + header.replace('"', '""') + '" TEXT' for header in headers)
    with sqlite3.connect(path) as connection:
        connection.execute(f"CREATE TABLE {quoted_table} ({declarations})")
        connection.executemany(f"INSERT INTO {quoted_table} VALUES ({','.join('?' for _ in headers)})", [
            [value.isoformat() if hasattr(value, "isoformat") else value for value in row] for row in rows[1:]
        ])
    return create_source(ctx, SourceInput(
        name=name.strip() or Path(filename).stem, type="sqlite", databaseName=database_filename,
        description=f"从用户上传的 {Path(filename).name} 导入，共 {len(rows) - 1} 行。保留原始文本；数值运算请显式转换字段类型。",
        allowedTables=[table_name],
    ))
