"""Persistent records for M23 system operations and M24 developer tools.

中文补充（系统运维与开发者工具的数据模型）：
- 三块业务：通知（模板/公告/收件箱）、调度（计划任务与运行记录）、
  开发者集成（客户端/令牌/证书/迁移批次与记录）。
- 唯一约束是幂等的基础，改动前请确认调用方仍然依赖：
  * uq_notification_template_code / uq_scheduled_job_code：工作区内 code 唯一；
  * uq_notice_recipient：一条公告对同一收件人只生成一条消息（重复发布会撞约束）；
  * uq_scheduled_job_run_key：(workspace_id, idempotency_key) 保证同一计划任务
    在某次触发窗口只产生一条运行记录；
  * uq_integration_client_id、uq_certificate_fingerprint、uq_migration_source_row
    分别保证客户端 ID、证书指纹、来源行（批次+表+来源键）不重复。
- 凭据只存摘要：客户端密钥与访问令牌都只落 sha256，明文不落库。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.ids import new_id
from app.models import Base, now
from app.modules.system.models import WorkspaceRecord


class NotificationTemplate(WorkspaceRecord, Base):
    """Reusable, versioned in-app notification text.

    中文补充：通知模板只做 {变量} 文本替换，不执行表达式；code 在工作区内唯一，
    停用（disabled）后不再被新通知引用，但历史引用仍可追溯。
    """

    __tablename__ = "system_notification_templates"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_notification_template_code"),)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="稳定模板编码")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="模板名称")
    subject_template: Mapped[str] = mapped_column(String(300), nullable=False, comment="标题模板")
    body_template: Mapped[str] = mapped_column(Text, nullable=False, comment="正文模板")
    variables: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="允许的变量名")
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, comment="active/disabled")
    created_by: Mapped[str] = mapped_column(String(26), nullable=False, comment="创建用户")


class SystemNotice(WorkspaceRecord, Base):
    """Draft or published announcement with an immutable delivery audience snapshot.

    中文补充：受众在保存时以 {"type","ids"} 快照存储，发布时据此一次性展开成收件箱消息；
    发布后不可修改（要改须复制为新公告），归档后不可再发布。
    """

    __tablename__ = "system_notices"
    title: Mapped[str] = mapped_column(String(300), nullable=False, comment="公告标题")
    body: Mapped[str] = mapped_column(Text, nullable=False, comment="公告正文")
    notice_type: Mapped[str] = mapped_column(String(32), default="announcement", nullable=False, comment="announcement/maintenance/security")
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False, comment="info/warning/critical")
    audience: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="all/roles/users 受众定义")
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, comment="draft/published/archived")
    publish_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="计划发布时间 UTC")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="公告到期时间 UTC")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="实际发布时间 UTC")
    created_by: Mapped[str] = mapped_column(String(26), nullable=False, comment="创建用户")


class InboxMessage(WorkspaceRecord, Base):
    """One recipient's append-only notice delivery and read state.

    中文补充：一条公告对每个收件人只生成一条消息（uq_notice_recipient），
    消息按收件人隔离读取，状态只在 unread/read/archived 之间单向流转。
    """

    __tablename__ = "system_inbox_messages"
    __table_args__ = (UniqueConstraint("workspace_id", "notice_id", "recipient_user_id", name="uq_notice_recipient"),)
    notice_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("system_notices.id"), nullable=True, index=True)
    recipient_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True, comment="收件用户")
    subject: Mapped[str] = mapped_column(String(300), nullable=False, comment="消息标题快照")
    body: Mapped[str] = mapped_column(Text, nullable=False, comment="消息正文快照")
    message_type: Mapped[str] = mapped_column(String(32), default="notice", nullable=False, comment="消息类型")
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False, comment="严重程度")
    status: Mapped[str] = mapped_column(String(24), default="unread", nullable=False, comment="unread/read/archived")
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="首次阅读时间 UTC")


class ScheduledJob(WorkspaceRecord, Base):
    """A versioned schedule pinned to an explicit business target and execution identity.

    中文补充：计划任务的"钉住"语义——保存时确定目标（工作流/业务动作）与执行身份
    （以哪个用户身份运行），此后目标被改动不会让已排期的任务悄悄换目标；
    修改计划必须显式重新选择并递增版本。
    """

    __tablename__ = "system_scheduled_jobs"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_scheduled_job_code"),)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="稳定任务编码")
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="任务名称")
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False, comment="任务用途")
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="workflow/alert/extraction/notification/health")
    target_id: Mapped[str] = mapped_column(String(80), default="", nullable=False, comment="固定业务资源标识")
    schedule_kind: Mapped[str] = mapped_column(String(16), nullable=False, comment="interval/cron")
    schedule_expression: Mapped[str] = mapped_column(String(160), nullable=False, comment="秒数或五段 cron")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False, comment="IANA 时区")
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="严格按任务类型校验的参数")
    status: Mapped[str] = mapped_column(String(24), default="paused", nullable=False, comment="active/paused/archived")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True, comment="下次计划时间 UTC")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近实际执行时间 UTC")
    created_by: Mapped[str] = mapped_column(String(26), nullable=False, comment="执行身份")


class ScheduledJobRun(Base):
    """Immutable execution attempt for a scheduled or manually triggered job.

    中文补充：运行记录只增不改；idempotency_key 唯一约束是"同一窗口只跑一次"的落地方式，
    调度器在执行外部动作前先提交 running 记录把键占住。
    """

    __tablename__ = "system_scheduled_job_runs"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key", name="uq_scheduled_job_run_key"),)
    # 幂等键一般由"计划任务 ID + 触发窗口"构成：唯一约束保证同一窗口不会重复派发。
    # 运行记录只增不改（immutable），失败原因写在新记录里而不是覆盖旧记录。
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(26), ForeignKey("system_scheduled_jobs.id"), nullable=False, index=True)
    triggered_by: Mapped[str] = mapped_column(String(26), nullable=False, comment="用户或 system")
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="manual/schedule")
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, comment="执行幂等键")
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False, comment="queued/running/completed/failed/skipped")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    output: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="脱敏执行摘要")
    error_summary: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="失败摘要")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class SystemBackup(WorkspaceRecord, Base):
    """Verified database backup artifact; restore is a separately confirmed plan.

    中文补充：备份条目只描述"文件 + 校验 + 元数据"，本身不触发恢复；
    恢复必须走独立确认计划（monitoring.restore_plan），避免误操作覆盖在用数据库。
    """

    __tablename__ = "system_backups"
    name: Mapped[str] = mapped_column(String(240), nullable=False, comment="备份名称")
    database_provider: Mapped[str] = mapped_column(String(32), nullable=False, comment="sqlite/mysql")
    database_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="来源数据库")
    storage_key: Mapped[str] = mapped_column(String(768), nullable=False, unique=True, comment="受管存储位置")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, comment="文件校验值")
    size: Mapped[int] = mapped_column(Integer, nullable=False, comment="字节数")
    status: Mapped[str] = mapped_column(String(24), default="verified", nullable=False, comment="creating/verified/failed")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="表数量与一致性摘要")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(26), nullable=False)


class IntegrationClient(WorkspaceRecord, Base):
    """OAuth-style local client; plaintext secret is returned once and never stored.

    中文补充：只保存 secret_digest（sha256）与 secret_prefix（界面辨认用），
    因此"忘记密钥"只能轮换，无法找回；scopes 是权限快照，调用时按创建者身份生效。
    """

    __tablename__ = "system_integration_clients"
    __table_args__ = (UniqueConstraint("workspace_id", "client_id", name="uq_integration_client_id"),)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    client_id: Mapped[str] = mapped_column(String(120), nullable=False)
    secret_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    redirect_uris: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(26), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IntegrationAccessToken(Base):
    """Short-lived service token bound to one client, workspace and scope snapshot.

    中文补充：只存 token_digest（sha256）与过期时间；scopes 是换取时的客户端权限快照，
    客户端轮换密钥或被停用时，其未撤销令牌会被批量置 revoked_at（立即失效）。
    """

    __tablename__ = "system_integration_access_tokens"
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    client_record_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("system_integration_clients.id"), nullable=False, index=True,
    )
    actor_user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class TrustedCertificate(WorkspaceRecord, Base):
    """Public certificate metadata. Private keys are never accepted or persisted here.

    中文补充：本表只存公开证书（pem 字段是证书本体，不含私钥）；上传私钥会被接口
    直接拒绝，因此这里不存在任何密钥材料。
    """

    __tablename__ = "system_trusted_certificates"
    __table_args__ = (UniqueConstraint("workspace_id", "fingerprint_sha256", name="uq_certificate_fingerprint"),)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, comment="trusted_ca/client_signing")
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(1000), nullable=False)
    issuer: Mapped[str] = mapped_column(String(1000), nullable=False)
    serial_number: Mapped[str] = mapped_column(String(160), nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    not_after: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    pem: Mapped[str] = mapped_column(Text, nullable=False, comment="仅公开证书 PEM")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(26), nullable=False)


class MigrationBatch(Base):
    """One immutable source scan plus its normalized import summary.

    中文补充：记录本次只读扫描的表级计数、总行数、归档行数与 checksum（表名+来源键+
    行哈希串联），用于事后核对归档完整性；summary 里固定声明 sourceMutations=0。
    """

    __tablename__ = "system_migration_batches"
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="mysql/sql_dump/sqlite")
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_schema: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, comment="scanning/ready/importing/completed/failed")
    table_counts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    imported_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    archived_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    options: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(26), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MigrationRecord(Base):
    """Sanitized source row with a stable hash and optional normalized target binding.

    中文补充：payload 是脱敏后的行内容（敏感字段只留 sha256 标记）；source_key 由主键
    拼出（无主键时为行号），与 batch_id + source_table 共同唯一，因此重复扫描同一批次
    不会产生重复行。
    """

    __tablename__ = "system_migration_records"
    __table_args__ = (UniqueConstraint("batch_id", "source_table", "source_key", name="uq_migration_source_row"),)
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(String(26), ForeignKey("system_migration_batches.id"), nullable=False, index=True)
    # Keep the composite unique key below MySQL's utf8mb4 index-byte limit.
    source_table: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    source_key: Mapped[str] = mapped_column(String(360), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, comment="敏感字段仅保留摘要")
    status: Mapped[str] = mapped_column(String(24), nullable=False, comment="archived/imported/skipped/failed")
    target_type: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    target_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    error_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
