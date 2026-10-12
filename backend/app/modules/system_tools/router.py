"""M23 system operations and M24 developer-integration APIs.

中文补充（系统运维与开发集成 HTTP 层，前缀 ``/api/v1/system-tools``）：
- 覆盖四类能力：通知（模板/公告/收件箱）、计划调度（任务与运行记录）、
  监控与备份（健康检查、缓存摘要、整库备份）、开发集成（客户端/令牌/证书/
  迁移来源扫描/SDK 代码生成）。
- 统一错误语义见下方 responses 声明：401 未登录、403 缺权限、404 记录不存在或跨工作区、
  409 版本冲突/唯一键重复/状态不允许、422 字段或调度校验失败、503 本机依赖不可用。
- 鉴权：全部端点要求登录上下文；动作权限在服务层按 system.operations.view/manage、
  system.development.view/manage、system.admin 分层校验；整库备份与恢复计划
  只允许交互式平台管理员（客户端令牌即使带权限也被拒）。
- 本层不做业务判断，只做参数注入与响应包装（Envelope / 文件流 / 纯文本）。
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse

from app.common.deps import AuthContext, get_current_context
from app.common.contracts import Envelope

from . import codegen, integrations, migrations, monitoring, notifications, schedules
from .schemas import (
    BackupCreateInput, CertificateInput, ClientCodeInput, IntegrationClientInput,
    JobInput, JobRunInput, MigrationScanInput, NoticeInput, TemplateInput, VersionAction,
)


router = APIRouter(
    prefix="/api/v1/system-tools",
    tags=["系统运维与开发集成"],
    responses={
        401: {"description": "未登录或会话失效"},
        403: {"description": "缺少系统运维或开发集成权限"},
        404: {"description": "记录不存在或不属于当前工作区"},
        409: {"description": "版本冲突、唯一键重复或状态不允许"},
        422: {"description": "字段、调度、证书或来源配置校验失败"},
        503: {"description": "受检查的本机依赖当前不可用"},
    },
)
Context = Annotated[AuthContext, Depends(get_current_context)]
RecordId = Annotated[str, Path(min_length=1, max_length=26, description="当前工作区内的记录标识")]
Keyword = Annotated[str, Query(max_length=160, description="名称或编码搜索，不解释为 SQL")]


@router.get("/notification-templates", operation_id="system_tools_notification_templates_list", summary="查询站内通知模板")
def list_notification_templates(ctx: Context, keyword: Keyword = ""):
    """返回模板变量和当前版本；正文不执行任意表达式或脚本。"""
    return {"items": notifications.list_templates(ctx, keyword)}


@router.post("/notification-templates", status_code=201, operation_id="system_tools_notification_templates_create", summary="创建站内通知模板")
def create_notification_template(payload: TemplateInput, ctx: Context):
    """模板编码在工作区内唯一；只允许登记的花括号变量。"""
    return notifications.save_template(ctx, payload)


@router.patch("/notification-templates/{record_id}", operation_id="system_tools_notification_templates_update", summary="修改站内通知模板")
def update_notification_template(record_id: RecordId, payload: TemplateInput, ctx: Context):
    """按 version 乐观锁修改；停用模板仍保留历史公告快照。"""
    return notifications.save_template(ctx, payload, record_id)


@router.post("/notification-templates/{record_id}/disable", operation_id="system_tools_notification_templates_disable", summary="停用站内通知模板")
def disable_notification_template(record_id: RecordId, payload: VersionAction, ctx: Context):
    """执行可恢复的停用，不物理删除已有模板或消息。"""
    notifications.delete_template(ctx, record_id, payload.version)
    return {"ok": True}


@router.get("/notices", operation_id="system_tools_notices_list", summary="查询公告及投递统计")
def list_notices(
    ctx: Context, keyword: Keyword = "",
    status: Annotated[Literal["", "draft", "published", "archived"], Query(description="公告状态")] = "",
):
    """管理员查看公告正文、受众快照与投递数，不返回其他工作区数据。"""
    return {"items": notifications.list_notices(ctx, keyword, status)}


@router.post("/notices", status_code=201, operation_id="system_tools_notices_create", summary="创建公告草稿")
def create_notice(payload: NoticeInput, ctx: Context):
    """创建时不自动发布；角色或用户受众必须属于当前工作区。"""
    return notifications.save_notice(ctx, payload)


@router.get("/notices/{record_id}", operation_id="system_tools_notices_get", summary="查看公告详情")
def get_notice(record_id: RecordId, ctx: Context):
    """同时返回不可变投递数量；已发布正文不会随模板变化。"""
    return notifications.get_notice(ctx, record_id)


@router.patch("/notices/{record_id}", operation_id="system_tools_notices_update", summary="修改公告草稿")
def update_notice(record_id: RecordId, payload: NoticeInput, ctx: Context):
    """只能修改草稿；已发布或归档公告必须复制为新记录。"""
    return notifications.save_notice(ctx, payload, record_id)


@router.post("/notices/{record_id}/publish", operation_id="system_tools_notices_publish", summary="发布公告并生成站内消息")
def publish_notice(record_id: RecordId, payload: VersionAction, ctx: Context):
    """发布时固定当前有效受众并逐人投递；重复提交返回已有投递结果。"""
    return notifications.publish_notice(ctx, record_id, payload.version)


@router.post("/notices/{record_id}/archive", operation_id="system_tools_notices_archive", summary="归档公告")
def archive_notice(record_id: RecordId, payload: VersionAction, ctx: Context):
    """归档不删除已投递消息和阅读状态。"""
    return notifications.archive_notice(ctx, record_id, payload.version)


@router.get("/inbox", operation_id="system_tools_inbox_list", summary="查看自己的站内消息")
def inbox(
    ctx: Context,
    status: Annotated[Literal["", "unread", "read"], Query(description="阅读状态")] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
):
    """所有登录成员只能读取自己的收件箱，并获得未读数量。"""
    return notifications.inbox(ctx, status=status, limit=limit, offset=offset)


@router.post("/inbox/{record_id}/read", operation_id="system_tools_inbox_read", summary="标记自己的消息已读")
def read_message(record_id: RecordId, payload: VersionAction, ctx: Context):
    """首次阅读记录时间；不能修改其他用户的消息。"""
    return notifications.change_message(ctx, record_id, payload.version, "read")


@router.post("/inbox/{record_id}/archive", operation_id="system_tools_inbox_archive", summary="归档自己的消息")
def archive_message(record_id: RecordId, payload: VersionAction, ctx: Context):
    """从正常收件箱隐藏消息但保留审计所需的投递记录。"""
    return notifications.change_message(ctx, record_id, payload.version, "archive")


@router.get("/jobs", operation_id="system_tools_jobs_list", summary="查询调度任务")
def list_jobs(
    ctx: Context, keyword: Keyword = "",
    status: Annotated[Literal["", "active", "paused"], Query(description="任务状态")] = "",
):
    """返回固定业务目标、时区、下次运行时间和乐观锁版本。"""
    return {"items": schedules.list_jobs(ctx, keyword, status)}


@router.post("/jobs", status_code=201, operation_id="system_tools_jobs_create", summary="创建受控调度任务")
def create_job(payload: JobInput, ctx: Context):
    """支持五段 cron 或秒级间隔，并固定到已发布工作流、抽取任务、公告或健康检查。"""
    return schedules.save_job(ctx, payload)


@router.patch("/jobs/{record_id}", operation_id="system_tools_jobs_update", summary="修改调度任务")
def update_job(record_id: RecordId, payload: JobInput, ctx: Context):
    """重新校验目标修订与参数；不允许修改已归档任务。"""
    return schedules.save_job(ctx, payload, record_id)


@router.post("/jobs/{record_id}/status/{action}", operation_id="system_tools_jobs_change_status", summary="启用、暂停或归档调度任务")
def change_job(
    record_id: RecordId,
    action: Annotated[Literal["activate", "pause", "archive"], Path(description="状态操作")],
    payload: VersionAction, ctx: Context,
):
    """每次状态变更校验 version；归档是终态且保留历史运行。"""
    return schedules.change_job(ctx, record_id, payload.version, action)


@router.post("/jobs/{record_id}/run", operation_id="system_tools_jobs_run", summary="手动执行一次调度任务")
def run_job(record_id: RecordId, payload: JobRunInput, ctx: Context):
    """幂等键先持久化再执行外部副作用；相同键重复请求返回同一运行记录。"""
    return schedules.run_job(ctx, record_id, payload.version, payload.idempotency_key)


@router.get("/jobs/{record_id}/runs", operation_id="system_tools_job_runs_list", summary="查看调度任务运行日志")
def list_job_runs(record_id: RecordId, ctx: Context, limit: Annotated[int, Query(ge=1, le=200)] = 100):
    """运行输出有大小上限，失败只保留脱敏错误摘要。"""
    return {"items": schedules.list_runs(ctx, record_id, limit)}


@router.get("/health", operation_id="system_tools_health_get", summary="检查服务与依赖健康状态")
def health(ctx: Context):
    """限时检查数据库、Redis、搜索和存储；不返回连接密码、缓存键或业务数据。"""
    return monitoring.health_snapshot(ctx)


@router.get("/cache", operation_id="system_tools_cache_get", summary="查看缓存容量摘要")
def cache(ctx: Context):
    """只返回当前 Redis 库的键数、过期数、内存和客户端数量。"""
    return monitoring.cache_summary(ctx)


@router.get("/backups", operation_id="system_tools_backups_list", summary="查询已验证数据库备份")
def list_backups(ctx: Context):
    """返回校验值、大小和结构清单，不公开受管存储绝对路径。"""
    return {"items": monitoring.list_backups(ctx)}


@router.post("/backups", status_code=201, operation_id="system_tools_backups_create", summary="创建并验证数据库备份")
def create_backup(payload: BackupCreateInput, ctx: Context):
    """需要显式确认；SQLite 使用在线备份，MySQL 使用单事务转储，完成后计算 SHA-256。"""
    return monitoring.create_backup(ctx, payload.name)


@router.get("/backups/{record_id}/download", operation_id="system_tools_backups_download", summary="下载校验通过的数据库备份")
def download_backup(record_id: RecordId, ctx: Context):
    """下载前再次计算 SHA-256；文件缺失或被修改时拒绝返回。"""
    row, path = monitoring.backup_path(ctx, record_id)
    return FileResponse(path, filename=row.name + (".sqlite3" if row.database_provider == "sqlite" else ".sql"))


@router.get("/backups/{record_id}/restore-plan", operation_id="system_tools_backups_restore_plan", summary="生成离线恢复计划")
def backup_restore_plan(record_id: RecordId, ctx: Context):
    """不会覆盖运行中的数据库；给出恢复到新库、兼容检查、切换和回退步骤。"""
    return monitoring.restore_plan(ctx, record_id)


@router.get("/integration-clients", operation_id="system_tools_clients_list", summary="查询接口认证客户端")
def list_clients(ctx: Context, keyword: Keyword = ""):
    """只显示客户端标识和密钥前缀，不返回已保存密钥明文。"""
    return {"items": integrations.list_clients(ctx, keyword)}


@router.post("/integration-clients", status_code=201, operation_id="system_tools_clients_create", summary="创建接口认证客户端")
def create_client(payload: IntegrationClientInput, ctx: Context):
    """密钥仅在本次响应显示一次，数据库只保存 SHA-256 摘要；权限不能超过创建者。"""
    return integrations.create_client(ctx, payload)


@router.patch("/integration-clients/{record_id}", operation_id="system_tools_clients_update", summary="修改认证客户端范围")
def update_client(record_id: RecordId, payload: IntegrationClientInput, ctx: Context):
    """回调地址默认要求 HTTPS；本机 localhost 可使用 HTTP。"""
    return integrations.update_client(ctx, record_id, payload)


@router.post("/integration-clients/{record_id}/rotate", operation_id="system_tools_clients_rotate", summary="轮换认证客户端密钥")
def rotate_client(record_id: RecordId, payload: VersionAction, ctx: Context):
    """新密钥仅显示一次并立即替换旧摘要；停用客户端不能轮换。"""
    return integrations.rotate_secret(ctx, record_id, payload.version)


@router.post("/integration-clients/{record_id}/disable", operation_id="system_tools_clients_disable", summary="停用认证客户端")
def disable_client(record_id: RecordId, payload: VersionAction, ctx: Context):
    """保留客户端和审计历史，后续令牌交换必须拒绝该客户端。"""
    return integrations.disable_client(ctx, record_id, payload.version)


@router.get("/certificates", operation_id="system_tools_certificates_list", summary="查询可信公开证书")
def list_certificates(ctx: Context):
    """只返回公开证书元数据与指纹，不接受或展示私钥。"""
    return {"items": integrations.list_certificates(ctx)}


@router.post("/certificates", status_code=201, operation_id="system_tools_certificates_create", summary="登记可信 X.509 公开证书")
def create_certificate(payload: CertificateInput, ctx: Context):
    """解析 PEM、计算 SHA-256 指纹并拒绝任何 PRIVATE KEY 内容。"""
    return integrations.add_certificate(ctx, payload)


@router.post("/certificates/{record_id}/disable", operation_id="system_tools_certificates_disable", summary="停用可信证书")
def disable_certificate(record_id: RecordId, payload: VersionAction, ctx: Context):
    """停用不删除证书历史，便于追踪曾经的信任配置。"""
    return integrations.disable_certificate(ctx, record_id, payload.version)


@router.get("/migration/sources", operation_id="system_tools_migration_sources", summary="查看本机只读迁移来源")
def migration_sources(ctx: Context):
    """仅列出配置白名单与目标库名称，不返回数据库密码或连接串。"""
    return migrations.allowed_sources(ctx)


@router.get("/migration/batches", operation_id="system_tools_migration_batches_list", summary="查询来源数据迁移批次")
def migration_batches(ctx: Context, limit: Annotated[int, Query(ge=1, le=200)] = 100):
    """返回每批表数、行数、脱敏归档数和整体校验值。"""
    return {"items": migrations.list_batches(ctx, limit)}


@router.post("/migration/batches", status_code=201, operation_id="system_tools_migration_batches_create", summary="只读扫描并归档来源数据库")
def migration_scan(payload: MigrationScanInput, ctx: Context):
    """只连接白名单库执行 SELECT；敏感字段仅保存摘要，绝不修改来源库。"""
    return migrations.scan_source(ctx, payload)


@router.get("/migration/batches/{record_id}", operation_id="system_tools_migration_batches_get", summary="查看迁移批次详情")
def migration_batch(record_id: RecordId, ctx: Context):
    """批次包含来源校验、完成状态和零来源写入声明。"""
    return migrations.get_batch(ctx, record_id)


@router.get("/migration/batches/{record_id}/records", operation_id="system_tools_migration_records_list", summary="分页查看脱敏来源记录")
def migration_records(
    record_id: RecordId, ctx: Context,
    table: Annotated[str, Query(max_length=120, description="来源表名，留空查询全部")] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
):
    """只返回批次中的脱敏副本和目标绑定，不回查或改写来源数据库。"""
    return migrations.list_records(ctx, record_id, table=table, limit=limit, offset=offset)


@router.get("/api-directory", operation_id="system_tools_api_directory", summary="查询完整接口目录与示例")
def api_directory(
    request: Request, ctx: Context, keyword: Keyword = "",
    tag: Annotated[str, Query(max_length=120, description="按 OpenAPI 分组筛选")] = "",
):
    """目录直接读取已挂载路由的 OpenAPI，含方法、路径、说明与请求/响应示例。"""
    return codegen.api_directory(ctx, request.app.openapi(), keyword_text=keyword, tag=tag)


@router.post("/client-code", response_class=PlainTextResponse, operation_id="system_tools_client_code_create", summary="生成 Python 或 TypeScript 接口客户端")
def client_code(payload: ClientCodeInput, request: Request, ctx: Context):
    """只接受真实 operationId；生成代码不内嵌令牌、密码或工作区标识。"""
    filename, content = codegen.generate_client(ctx, request.app.openapi(), payload)
    return PlainTextResponse(content, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
