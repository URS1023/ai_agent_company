"""Bounded dependency checks and verified, non-destructive database backups.

中文补充（健康检查与备份）：
- 健康快照：只检查已配置依赖（数据库/Redis/检索/存储）的连通性，返回耗时与状态，
  不泄露凭据、不暴露缓存键值；判定口径为"database 与 storage 必须健康"，
  其余依赖降级只导致 degraded 而不是 unhealthy。
- 缓存摘要：仅返回 Redis 计数指标（键数/过期数/内存/连接数/版本），
  绝不返回键名或键值——避免把业务数据外泄到运维页面。
- 备份只读边界：整库备份要求"交互式平台管理员"（system.admin + role=admin +
  非客户端令牌 + 同时是所有组织与工作区管理员），因为 dump 里含其它工作区数据；
  下载前会重新校验 storage 文件存在且 sha256 与记录一致，不一致即拒绝。
- 恢复：只产出"需离线执行"的恢复计划（automaticApply=False、status=
  requires_offline_restart），绝不在运行中的数据库上原地覆盖。
- 上限：备份文件必须大于 0 且不超过 _MAX_BACKUP_BYTES(1 GiB)，超限拒绝而不是截断。
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import exists, func, inspect, select, text

from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.common.storage import build_key, get_storage
from app.config import settings
from app.db import get_engine
from app.errors import AppError, ConflictError, NotFoundError, PermissionErrorEx, ValidationError
from app.ids import new_id
from app.models import Organization, OrganizationMember, Workspace, WorkspaceMember
from app.modules.system.service_helpers import commit_change, flush_changes

from .models import SystemBackup


_STARTED_AT = datetime.utcnow()
_MAX_BACKUP_BYTES = 1024 * 1024 * 1024


class DependencyUnavailable(AppError):
    """依赖不可用（HTTP 503）：Redis/检索等非核心依赖探测失败时抛出。

    用 503 而不是 500，语义是"当前暂时不可用、可稍后重试"，且响应里只带依赖名与
    异常类型（不泄露连接串或驱动堆栈）。
    """

    status_code = 503
    code = "dependency_unavailable"


def _iso(value: datetime | None) -> str | None:
    """时间转 ISO 字符串（None 保持 None）。"""
    return value.isoformat() if value else None


def _timed(check) -> dict:
    """执行一次依赖探测并附上耗时；失败不抛出，而是转成 unavailable + 错误类型。

    健康检查必须"永不 500"：任一依赖探测失败都要能被快照函数如实呈现为不可用状态。
    """
    started = time.perf_counter()
    try:
        detail = check() or {}
        return {"status": "healthy", "latencyMs": round((time.perf_counter() - started) * 1000, 2), **detail}
    except Exception as exc:
        return {
            "status": "unavailable", "latencyMs": round((time.perf_counter() - started) * 1000, 2),
            "error": type(exc).__name__, "message": str(exc)[:240],
        }


def _database_check(ctx: AuthContext) -> dict:
    """数据库探测：执行 SELECT 1 并回传方言与库名（不含连接串与凭据）。"""
    ctx.db.execute(text("SELECT 1")).scalar_one()
    engine = get_engine()
    return {"provider": engine.dialect.name, "database": engine.url.database or ":memory:"}


def _redis_check() -> dict:
    """Redis 探测：ping + 读取服务端版本；连接超时 1.5 秒，finally 关闭连接。"""
    import redis

    client = redis.Redis.from_url(settings.REDIS_URL, protocol=2, socket_connect_timeout=1.5, socket_timeout=1.5)
    try:
        if not client.ping():
            raise RuntimeError("Redis ping 未返回成功")
        info = client.info(section="server")
        return {"version": info.get("redis_version", ""), "database": urlparse(settings.REDIS_URL).path.lstrip("/") or "0"}
    finally:
        client.close()


def _search_check() -> dict:
    """检索服务探测：调用集群健康接口（超时 2 秒），返回集群名与状态。"""
    response = httpx.get(settings.ES_URL.rstrip("/") + "/_cluster/health", timeout=2.0)
    response.raise_for_status()
    data = response.json()
    return {"cluster": data.get("cluster_name", ""), "clusterStatus": data.get("status", "unknown")}


def _storage_check() -> dict:
    """存储探测：写入并读回一个探针对象（验证读写一致），再报告磁盘余量。

    探针文件写在 storage 根下的 health/ 前缀，读取内容不一致视为存储异常。
    """
    storage = get_storage()
    key = "health/.probe-" + new_id()
    storage.put_bytes(key, b"ok")
    try:
        if storage.read_bytes(key) != b"ok":
            raise RuntimeError("存储读回内容不一致")
        usage = shutil.disk_usage(storage.root)
        return {"provider": storage.provider, "root": str(storage.root), "freeBytes": usage.free, "totalBytes": usage.total}
    finally:
        storage.delete(key)


def health_snapshot(ctx: AuthContext) -> dict:
    """Check configured dependencies without exposing credentials or arbitrary cache contents.

    中文补充：权限 system.operations.view；返回 dependencies（各依赖状态与耗时）、
    process（pid/python/platform/启动时间/运行秒数）。
    status 为 healthy 当且仅当 database 与 storage 都健康，否则 degraded——
    这两个是平台可用性的硬依赖，Redis/检索不可用只影响部分功能。
    副作用：对外发起连接探测（有超时保护），不写库。
    """
    require_permission(ctx, "system.operations.view")
    dependencies = {
        "database": _timed(lambda: _database_check(ctx)),
        "redis": _timed(_redis_check),
        "search": _timed(_search_check),
        "storage": _timed(_storage_check),
    }
    required = ("database", "storage")
    status = "healthy" if all(dependencies[key]["status"] == "healthy" for key in required) else "degraded"
    return {
        "status": status,
        "generatedAt": datetime.utcnow().isoformat(),
        "process": {
            "pid": os.getpid(), "python": platform.python_version(), "platform": platform.platform(),
            "startedAt": _STARTED_AT.isoformat(),
            "uptimeSeconds": int((datetime.utcnow() - _STARTED_AT).total_seconds()),
        },
        "dependencies": dependencies,
    }


def cache_summary(ctx: AuthContext) -> dict:
    """Return Redis database counters only; keys and values remain undisclosed.

    中文补充：权限 system.operations.view；只回传计数类指标（当前 db 的键数/过期数、
    已用内存、连接数、版本）。
    异常：Redis 不可用时抛 DependencyUnavailable（带 dependency 与异常类型），
    由上层转成"依赖不可用"提示，而不是把驱动堆栈透给前端。
    """
    require_permission(ctx, "system.operations.view")
    import redis

    client = None
    try:
        client = redis.Redis.from_url(settings.REDIS_URL, protocol=2, socket_connect_timeout=1.5, socket_timeout=1.5)
        info = client.info()
        current = urlparse(settings.REDIS_URL).path.lstrip("/") or "0"
        db = info.get("db" + current, {})
        return {
            "status": "healthy", "database": int(current), "keys": int(db.get("keys", 0)),
            "expires": int(db.get("expires", 0)), "usedMemory": int(info.get("used_memory", 0)),
            "clients": int(info.get("connected_clients", 0)), "version": info.get("redis_version", ""),
        }
    except Exception as exc:
        raise DependencyUnavailable("Redis 当前不可用", dependency="redis", reason=type(exc).__name__) from exc
    finally:
        if client is not None:
            client.close()


def _require_platform_backup_access(ctx: AuthContext, *, manage: bool = False) -> None:
    """Require an interactive platform administrator for whole-database files.

    A workspace operations role cannot safely receive a dump that contains
    records from other workspaces.  ``system.admin`` is additionally required
    so a member role with only backup-adjacent permissions cannot reach this
    path; service-client tokens are excluded even when their scope lists it.

    中文补充：整库备份/下载/恢复的准入闸门。要求 system.admin + 交互式 admin 角色
    （客户端令牌即使 scope 里有 system.admin 也被拒）+ 当前用户是所有组织与所有工作区
    的管理员；``manage=True`` 时额外要求 system.operations.manage。
    异常：不满足任一条件抛 PermissionErrorEx。
    """
    require_permission(ctx, "system.admin")
    if ctx.client_id or ctx.role != "admin":
        raise PermissionErrorEx("整库备份仅允许平台管理员交互会话")
    missing_organization_admin = ctx.db.scalar(select(Organization.id).where(
        ~exists().where(
            OrganizationMember.organization_id == Organization.id,
            OrganizationMember.user_id == ctx.user_id,
            OrganizationMember.role == "admin",
        ),
    ).limit(1))
    missing_workspace_admin = ctx.db.scalar(select(Workspace.id).where(
        ~exists().where(
            WorkspaceMember.workspace_id == Workspace.id,
            WorkspaceMember.user_id == ctx.user_id,
            WorkspaceMember.role == "admin",
        ),
    ).limit(1))
    if missing_organization_admin is not None or missing_workspace_admin is not None:
        raise PermissionErrorEx("整库备份需要库内所有组织和工作区管理员权限")
    if manage:
        require_permission(ctx, "system.operations.manage")


def _backup_manifest(ctx: AuthContext) -> dict:
    """备份清单：表数量、表名列表与已应用的结构迁移版本（供恢复前比对）。

    只读查询平台库；schema_migrations 不存在时（早期库）返回空列表而不是报错。
    """
    inspector = inspect(get_engine())
    tables = sorted(inspector.get_table_names())
    migrations = []
    if "schema_migrations" in tables:
        migrations = [str(row[0]) for row in ctx.db.execute(text(
            "SELECT version FROM schema_migrations ORDER BY applied_at, version"
        )).all()]
    return {"tableCount": len(tables), "tables": tables, "schemaVersions": migrations}


def _sqlite_dump(path: Path) -> None:
    """用 SQLite 在线备份 API 导出到目标文件，并立即做 integrity_check。

    异常：完整性检查不通过抛 RuntimeError（备份不可用，宁可失败也不产出坏备份）。
    """
    raw = get_engine().raw_connection()
    target = sqlite3.connect(path)
    try:
        source = getattr(raw, "driver_connection", None) or getattr(raw, "connection", raw)
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError("SQLite 备份完整性检查失败")
    finally:
        target.close()
        raw.close()


def _mysql_dump(path: Path) -> None:
    """调用 mysqldump 生成一致性快照（--single-transaction --skip-lock-tables）。

    密码通过子进程环境变量 MYSQL_PWD 传递（不出现在命令行与日志里），并在 run 返回后
    从环境副本中清除。找不到 mysqldump 或返回码非 0 时抛 RuntimeError。
    """
    executable = shutil.which("mysqldump")
    if not executable:
        raise RuntimeError("未找到 mysqldump，无法创建一致性备份")
    engine = get_engine()
    args = [
        executable, "--host", engine.url.host or settings.MYSQL_HOST,
        "--port", str(engine.url.port or settings.MYSQL_PORT), "--user", engine.url.username or settings.MYSQL_USER,
        "--default-character-set=utf8mb4", "--single-transaction", "--skip-lock-tables",
        "--routines", "--events", "--hex-blob", "--set-gtid-purged=OFF",
        "--result-file", str(path), str(engine.url.database),
    ]
    environment = os.environ.copy()
    environment["MYSQL_PWD"] = engine.url.password or settings.MYSQL_PASSWORD
    result = subprocess.run(args, capture_output=True, text=True, timeout=300, env=environment, check=False)
    environment.pop("MYSQL_PWD", None)
    if result.returncode:
        raise RuntimeError((result.stderr or "mysqldump 执行失败")[:1000])


def create_backup(ctx: AuthContext, name: str) -> dict:
    """创建整库备份并登记为 verified 记录；权限见 _require_platform_backup_access(manage=True)。

    流程：在临时目录生成 dump → 校验大小（0 < size ≤ 1 GiB）→ 计算 sha256 →
    写入对象存储 → 落库（status=verified，manifest 记录表清单与迁移版本）。
    返回备份视图；异常：数据库类型不支持抛 ValidationError，dump 失败/超限同样拒绝。
    副作用：导出整库文件到临时目录后写入 storage（含其它工作区数据，故权限要求极高）。
    """
    _require_platform_backup_access(ctx, manage=True)
    engine = get_engine()
    provider = engine.dialect.name
    if provider not in {"sqlite", "mysql"}:
        raise ValidationError("当前数据库类型尚未提供受控备份器")
    suffix = ".sqlite3" if provider == "sqlite" else ".sql"
    record_id = new_id()
    with tempfile.TemporaryDirectory(prefix="test5-backup-") as directory:
        path = Path(directory) / ("platform" + suffix)
        if provider == "sqlite":
            _sqlite_dump(path)
        else:
            _mysql_dump(path)
        size = path.stat().st_size
        if size <= 0 or size > _MAX_BACKUP_BYTES:
            raise ValidationError("备份文件为空或超过 1 GiB 安全上限")
        data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    filename = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-" + name.strip() + suffix
    key = build_key("system-backups", ctx.workspace_id, record_id, filename)
    get_storage().put_bytes(key, data)
    row = SystemBackup(
        id=record_id, workspace_id=ctx.workspace_id, name=name.strip(), database_provider=provider,
        database_name=engine.url.database or ":memory:", storage_key=key, sha256=digest, size=size,
        status="verified", manifest=_backup_manifest(ctx), verified_at=datetime.utcnow(), created_by=ctx.user_id,
    )
    ctx.db.add(row)
    flush_changes(ctx)
    commit_change(ctx, "system.backup.create", "system_backup", row.id,
                  {"provider": provider, "size": size, "sha256": digest})
    return backup_view(row)


def backup_view(row: SystemBackup) -> dict:
    """备份视图：含 sha256/size/manifest 与下载路径，不含任何凭据。"""
    return {
        "id": row.id, "name": row.name, "databaseProvider": row.database_provider,
        "databaseName": row.database_name, "sha256": row.sha256, "size": row.size,
        "status": row.status, "manifest": row.manifest, "verifiedAt": _iso(row.verified_at),
        "version": row.version, "createTime": _iso(row.created_at),
        "downloadPath": f"/api/v1/system-tools/backups/{row.id}/download",
    }


def list_backups(ctx: AuthContext) -> list[dict]:
    """列出本工作区备份记录（最多 100 条，按创建时间倒序）；权限同整库备份准入。"""
    _require_platform_backup_access(ctx)
    rows = ctx.db.scalars(select(SystemBackup).where(
        SystemBackup.workspace_id == ctx.workspace_id,
    ).order_by(SystemBackup.created_at.desc()).limit(100)).all()
    return [backup_view(row) for row in rows]


def get_backup(ctx: AuthContext, record_id: str) -> SystemBackup:
    """读取备份记录并做完整性复验；权限同整库备份准入。

    复验两件事：受管文件必须存在（否则报"记录存在但文件缺失"），
    且内容 sha256 必须与登记值一致（不一致抛 ConflictError 拒绝下载或恢复）。
    这是防止"备份被替换"的关键检查，不能省略。
    """
    _require_platform_backup_access(ctx)
    row = ctx.db.scalar(select(SystemBackup).where(
        SystemBackup.id == record_id, SystemBackup.workspace_id == ctx.workspace_id,
    ))
    if row is None:
        raise NotFoundError("备份不存在")
    storage = get_storage()
    if not storage.exists(row.storage_key):
        raise NotFoundError("备份记录存在，但受管文件缺失")
    current = hashlib.sha256(storage.read_bytes(row.storage_key)).hexdigest()
    if current != row.sha256:
        raise ConflictError("备份校验值不一致，拒绝下载或恢复")
    return row


def backup_path(ctx: AuthContext, record_id: str) -> tuple[SystemBackup, str]:
    """返回 (备份记录, 受管文件本地路径)，供下载/恢复使用（已通过完整性复验）。"""
    row = get_backup(ctx, record_id)
    return row, get_storage().get_path(row.storage_key)


def restore_plan(ctx: AuthContext, record_id: str) -> dict:
    """Build a restart-bound restore plan without mutating the active database.

    中文补充：只读地生成"离线恢复计划"：列出人工步骤（停服→复核校验值→恢复到新库→
    切换验证并保留原库回退），并明确 automaticApply=False。本函数不触碰当前数据库，
    也不执行任何恢复动作——运行中的库绝不允许被网页原地覆盖。
    """
    _require_platform_backup_access(ctx, manage=True)
    row = get_backup(ctx, record_id)
    return {
        "backupId": row.id, "status": "requires_offline_restart",
        "databaseProvider": row.database_provider, "databaseName": row.database_name,
        "sha256": row.sha256, "size": row.size,
        "steps": [
            "停止当前 API 服务并确认没有正在执行的工作流或写入任务",
            "再次校验备份 SHA-256 与目标数据库名称",
            "恢复到新数据库名称并执行结构兼容检查",
            "切换连接后启动服务，验证就绪接口，再保留原库作为回退",
        ],
        "automaticApply": False,
        "reason": "运行中的数据库不允许通过网页原地覆盖；恢复必须在离线维护窗口执行。",
    }
