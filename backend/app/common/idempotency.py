"""Atomic idempotency claims for side-effecting application entry points.

带副作用入口的原子幂等占位：让"同一幂等键"的并发/重试请求只有一个真正执行副作用。

系统位置：报告生成（``modules/reports``）与应用启动（``modules/applications``）等昂贵入口使用；
占位行存于 ``idempotency_claims``，唯一约束为 (workspace, owner, scope, key)。

关键不变式与约束：
- 必须先 commit 占位再执行副作用（否则并发请求会双双跑模型/查询/写文件）。
- ``request_hash`` 不同即视为键复用，直接 409，不做任何"聪明"的合并。
- 无论成功还是失败都必须调用 ``finish_idempotency``，否则键会永久停在 ``claimed``，
  后续同键请求只能收到"仍在处理中"。
- 重放只回放已授权的 ``result_json``，不要把内部错误细节写进去。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.common.deps import AuthContext
from app.errors import ConflictError
from app.ids import new_id
from app.models import IdempotencyClaim


@dataclass(frozen=True)
class ClaimResult:
    """一次幂等占位的结果：``claim`` 为持久化行，``reused`` 表示本次是否复用了已完成的键。

    不变式：``reused=True`` 只代表键已 ``completed``，调用方**不得**再执行副作用，
    应直接回放 ``claim.result_json``（并补一个 ``reused`` 标记告诉前端这是历史结果）；
    ``reused=False`` 表示本次抢到占位，必须由调用方在成功/失败后调用 ``finish_idempotency``。
    冻结 dataclass：不要在调用方改写字段，改数据库行才是唯一生效方式。
    """

    claim: IdempotencyClaim
    reused: bool


def _utc_now() -> datetime:
    """Return naive UTC for the existing SQLAlchemy DateTime contract.

    表字段是**无时区** DateTime，因此必须落 naive UTC：写入 aware 时间会在 SQLite/Postgres 上
    产生不一致的比较与序列化行为（这是历史契约，不改列类型就不要改这里的语义）。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def claim_idempotency(
    ctx: AuthContext,
    *,
    scope: str,
    key: str | None,
    request_hash: str,
) -> ClaimResult | None:
    """Atomically reserve ``key`` and commit it before doing any side effect.

    A unique-index collision is resolved only after rolling back the losing
    insert.  The winner is then replayed or reported as in progress/failed;
    the caller never starts a second model, query, run, or file operation.

    原子抢占幂等键，并在返回前提交占位（先占后用）。

    参数：``ctx`` 登录上下文（提供 ``db`` 会话、``workspace_id``、``user_id``）；
    ``scope`` 业务域（如 ``report_generation:manual``，避免不同入口的键互撞）；
    ``key`` 客户端幂等键，空白/未提供时返回 None 表示"本次不做幂等"；
    ``request_hash`` 请求参数指纹（用于识别同键改参数）。

    返回：``ClaimResult``（``reused=False`` → 已抢到，需执行副作用后 ``finish_idempotency``；
    ``reused=True`` → 键已完成，直接回放 ``claim.result_json``）；``None`` → 未启用幂等，
    调用方按"无幂等"路径执行（因此调用方需允许 claim 为 None）。

    业务异常：``ConflictError``（409）三种情形——同键但 ``request_hash`` 不同（键被复用于不同参数）、
    同键上次以失败结束（要求换新键重试）、同键仍在处理中（提示稍后取原结果）。
    若回滚后查不到冲突行（键属于其他 owner 或竞态残留），原始 ``IntegrityError`` 会继续上抛。

    副作用（重要）：insert + flush + **commit**——会一并提交调用方 session 中此前挂起的改动，
    所以必须在明确的提交点调用，不要塞进长事务中间；异常路径下会先 rollback 丢掉失败的那条 insert。
    """
    normalized = str(key or "").strip()
    if not normalized:
        return None
    claim = IdempotencyClaim(
        id=new_id(), workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        scope=scope, idempotency_key=normalized, request_hash=request_hash,
        status="claimed", created_at=_utc_now(), updated_at=_utc_now(),
    )
    ctx.db.add(claim)
    try:
        ctx.db.flush()
    except IntegrityError:
        ctx.db.rollback()
        existing = ctx.db.scalar(select(IdempotencyClaim).where(
            IdempotencyClaim.workspace_id == ctx.workspace_id,
            IdempotencyClaim.owner_user_id == ctx.user_id,
            IdempotencyClaim.scope == scope,
            IdempotencyClaim.idempotency_key == normalized,
        ))
        if existing is None:
            raise
        if existing.request_hash != request_hash:
            raise ConflictError(
                "同一幂等键已用于不同的请求参数", claimId=existing.id,
            )
        if existing.status == "completed":
            return ClaimResult(existing, True)
        if existing.status == "failed":
            raise ConflictError(
                "同一幂等键的请求已失败，请使用新的幂等键重试", claimId=existing.id,
            )
        raise ConflictError(
            "同一幂等键的请求仍在处理中，请稍后读取原请求结果", claimId=existing.id,
        )
    # Make the reservation visible to concurrent requests before the caller
    # invokes a model, collects HTTP data, or creates a Run.
    ctx.db.commit()
    return ClaimResult(claim, False)


def finish_idempotency(
    ctx: AuthContext,
    claim_id: str | None,
    *,
    status: str,
    result: dict | None = None,
    error_message: str = "",
    resource_type: str = "",
    resource_id: str | None = None,
) -> None:
    """Publish the result of a previously committed claim.

    回填并提交占位行的最终状态，让后续同键请求可以重放（``completed``）或被明确拒绝（``failed``）。

    参数：``ctx`` 登录上下文；``claim_id`` 占位行 id（``None`` 直接返回——便于无幂等路径与有幂等
    路径共用同一段收尾代码）；``status`` 终态（``completed``/``failed``）；``result`` 成功结果
    （写入 ``result_json``，只放已授权响应）；``error_message`` 失败说明（落库前截断到 4000 字符）；
    ``resource_type``/``resource_id`` 本次副作用产生的资源，便于按资源反查占位。

    返回：None。异常：**本函数不抛异常**——session 不可用（已回滚/连接失效）或行已不存在时静默返回，
    以保证失败收尾不会覆盖调用方正在抛出的原始业务异常（代价是极端情况下键会停在 ``claimed``）。

    副作用：session 非活跃时先 rollback，然后写行并 commit（独立于业务事务）。
    约束：成功与失败都必须调用，且要在异常处理的最外层调用（见 ``modules/reports`` 的用法）。
    """
    if not claim_id:
        return
    if not ctx.db.is_active:
        ctx.db.rollback()
    try:
        claim = ctx.db.get(IdempotencyClaim, claim_id)
    except Exception:
        # Preserve the original operation failure if the session itself is
        # unavailable; the next request will still see the durable claim.
        return
    if claim is None:
        return
    claim.status = status
    claim.error_message = str(error_message or "")[:4000]
    if result is not None:
        claim.result_json = result
    if resource_type:
        claim.resource_type = resource_type
    if resource_id:
        claim.resource_id = resource_id
    claim.updated_at = _utc_now()
    ctx.db.commit()
