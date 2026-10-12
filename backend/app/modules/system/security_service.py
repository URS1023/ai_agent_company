"""Login policy evaluation, trusted-device approval and security evidence.

职责：工作区级登录安全 —— IP 白/黑名单、可信设备审批、并发会话上限，以及登录事件与会话
安全上下文的记录，供管理员在“安全治理”页查看。

位置与依赖：对外路由前缀 /api/v1/system（governance_router.py）—— GET/PATCH /security/policy、
GET /security/devices、PATCH /security/devices/{id}、GET /security/login-events。
此外本模块的 client_ip/user_agent/hash_device/evaluate_login/record_success 会被登录链路
（app.modules.auth）与 OIDC 回调（oidc_service.finish_authorization）直接调用。
依赖 models 中的 WorkspaceSecurityPolicy/TrustedDevice/LoginEvent/SessionContext。

关键约束：
- 只信任 socket 对端地址：client_ip 刻意不读取 X-Forwarded-For 等可伪造头，因此本模块应部署在
  可信代理之后，由代理层负责真实来源地址的收敛。
- 设备只保存哈希（sha256("<workspace_id>:<设备标识>")），原始标识不入库；接口返回还会截断为 12 位。
- 安全策略只在登录时生效，不会主动踢出已签发的会话；被拒绝的登录会先提交事件记录再抛 AuthError，
  保证“拒绝”这件事本身有据可查。
- 本模块不存凭据：策略表里没有密码或密钥字段。
"""
from __future__ import annotations

import hashlib
import ipaddress
from datetime import datetime

from sqlalchemy import func, select

from app.common.audit import audit
from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.errors import AuthError, ConflictError, NotFoundError, ValidationError
from app.ids import new_id
from app.models import SessionToken, Workspace
from app.modules.system.models import (
    LoginEvent, SessionContext, TrustedDevice, WorkspaceSecurityPolicy,
)
from app.modules.system.security_schemas import DeviceDecision, SecurityPolicyUpdate
from app.modules.system.service_helpers import check_version


def client_ip(request) -> str:
    """Use the socket peer address; forwarded headers are intentionally not trusted here.

    用途：取客户端 IP。刻意不读 X-Forwarded-For/X-Real-IP（可被客户端伪造，会导致 IP 白名单
    形同虚设），只取 ASGI 提供的 socket 对端地址。
    返回：规范化后的 IP 字符串；IPv4-mapped IPv6 会还原成 IPv4；无法解析时返回截断到 64 字符的
    原始值（保证入库列长度安全）。异常：无（解析失败不抛错）。副作用：无。
    """
    value = str(getattr(getattr(request, "client", None), "host", "") or "").strip()
    try:
        address = ipaddress.ip_address(value)
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        return str(address)
    except ValueError:
        return value[:64]


def user_agent(request) -> str:
    """取 User-Agent 头并截断到 500 字符（与列长度一致），仅用于安全审计展示。"""
    return str(request.headers.get("user-agent", "") or "")[:500]


def hash_device(workspace_id: str, device_id: str | None) -> str:
    """把前端设备标识哈希成本地设备指纹。

    参数：workspace_id 参与加盐，使同一浏览器在不同工作区得到不同指纹（防跨工作区关联）；
    device_id 为空或空白时返回空字符串（表示无法识别设备，不会写入设备表）。
    返回：sha256("<workspace_id>:<device_id>") 的十六进制摘要（64 字符，与列长度一致）。
    副作用：无。约束：前端只需稳定上报一个设备标识即可，原始值永不出现在库与接口中。
    """
    clean = (device_id or "").strip()
    if not clean:
        return ""
    return hashlib.sha256(f"{workspace_id}:{clean}".encode("utf-8")).hexdigest()


def get_policy(db, workspace_id: str) -> WorkspaceSecurityPolicy:
    """取（或按需创建）工作区安全策略行。

    参数：db 会话；workspace_id 目标工作区。返回：策略行。
    异常：无显式业务异常。副作用：首次访问会插入一行默认策略并 flush（不提交，由调用方决定）；
    默认值即“策略未配置时的放行状态”：enable 相关判断都依赖调用方再检查 policy.enabled。
    """
    row = db.scalar(select(WorkspaceSecurityPolicy).where(
        WorkspaceSecurityPolicy.workspace_id == workspace_id,
    ))
    if row is None:
        row = WorkspaceSecurityPolicy(id=new_id(), workspace_id=workspace_id)
        db.add(row)
        db.flush()
    return row


def policy_out(row: WorkspaceSecurityPolicy) -> dict:
    """策略输出结构（GET/PATCH /security/policy 共用），含 version 供乐观锁提交。"""
    return {
        "id": row.id, "version": row.version, "enabled": row.enabled,
        "allowed_cidrs": row.allowed_cidrs, "blocked_cidrs": row.blocked_cidrs,
        "require_trusted_device": row.require_trusted_device,
        "max_failed_attempts": row.max_failed_attempts, "lock_minutes": row.lock_minutes,
        "max_active_sessions": row.max_active_sessions, "session_overflow": row.session_overflow,
        "updated_at": row.updated_at,
    }


def read_policy(ctx: AuthContext) -> dict:
    """读取工作区安全策略。

    用途：GET /api/v1/system/security/policy（需要 system.security.view）。
    参数：ctx。返回：policy_out。异常：PermissionErrorEx（缺少查看权限）。
    副作用：策略行不存在时创建默认行并提交（读取即初始化，避免前端拿到空配置）。
    """
    require_permission(ctx, "system.security.view")
    row = get_policy(ctx.db, ctx.workspace_id)
    ctx.db.commit()
    return policy_out(row)


def _validated_networks(values: list[str]) -> list[str]:
    """把网段字符串规范化为 CIDR 并去重（strict=False 允许 "192.168.1.5" 这类主机地址写法）。

    异常：ValidationError —— 任一网段无法解析（提示中包含原值，便于管理员定位）。
    副作用：无；纯函数，保证入库值只有一种规范形式，方便后续与 peer IP 比较。
    """
    result = []
    for value in values:
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError as exc:
            raise ValidationError(f"无效的 IP 网段：{value}") from exc
        normalized = str(network)
        if normalized not in result:
            result.append(normalized)
    return result


def update_policy(ctx: AuthContext, payload: SecurityPolicyUpdate) -> dict:
    """修改工作区登录策略。

    用途：PATCH /api/v1/system/security/policy（需要 system.security.manage）。
    参数：ctx；payload 为 SecurityPolicyUpdate（version 必填，未传字段保持原值）。
    返回：更新后的 policy_out。
    异常：ValidationError（网段非法、同一网段同时出现在允许与阻止列表）；ConflictError（版本冲突）；
    PermissionErrorEx（缺少管理权限）。
    副作用：更新策略行并写 security.policy_update 审计（记录变更字段名，不记录具体值），然后提交；
    策略变更只影响之后的登录，不会中断已签发会话。
    """
    require_permission(ctx, "system.security.manage")
    row = get_policy(ctx.db, ctx.workspace_id)
    check_version(row, payload.version)
    values = payload.model_dump(exclude_unset=True)
    values.pop("version", None)
    if "allowed_cidrs" in values:
        values["allowed_cidrs"] = _validated_networks(values["allowed_cidrs"] or [])
    if "blocked_cidrs" in values:
        values["blocked_cidrs"] = _validated_networks(values["blocked_cidrs"] or [])
    overlap = set(values.get("allowed_cidrs", row.allowed_cidrs)) & set(values.get("blocked_cidrs", row.blocked_cidrs))
    if overlap:
        raise ValidationError("同一网段不能同时位于允许和阻止列表", cidrs=sorted(overlap))
    for key, value in values.items():
        setattr(row, key, value)
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="security.policy_update", resource_type="security_policy", resource_id=row.id,
          metadata={"fields": sorted(values)})
    ctx.db.commit()
    return policy_out(row)


def _ip_matches(ip_value: str, networks: list[str]) -> bool:
    """判断 IP 是否落在任一网段内；IP 无法解析时返回 False（不匹配即按“不在名单”处理）。"""
    try:
        address = ipaddress.ip_address(ip_value)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(value, strict=False) for value in networks)


def _event(db, *, workspace_id: str, user_id: str | None, username: str,
           auth_method: str, outcome: str, reason: str, risk_level: str,
           ip_address: str, device_hash: str, user_agent_value: str,
           details: dict | None = None) -> LoginEvent:
    """构造一条登录事件（只 add，不提交；提交时机由调用方控制）。

    参数：outcome ∈ success/failed/blocked；reason 是稳定机器码（如 blocked_ip、
    device_approval_required），前端据此做中文映射；details 为补充证据（如待审批设备 id）。
    返回：LoginEvent 实例。约束：所有自由文本都会按列长截断（username 200 / reason 120 /
    ip 64 / ua 500），避免超长输入导致写入失败；严禁把密码或令牌放入 details。
    """
    row = LoginEvent(
        id=new_id(), workspace_id=workspace_id, user_id=user_id,
        username=username[:200], auth_method=auth_method, outcome=outcome,
        reason=reason[:120], risk_level=risk_level, ip_address=ip_address[:64],
        device_hash=device_hash, user_agent=user_agent_value[:500], details=details or {},
    )
    db.add(row)
    return row


def record_failed_login(db, *, workspace_id: str, user_id: str | None, username: str,
                        auth_method: str, request, device_id: str | None,
                        reason: str = "invalid_credentials") -> None:
    """记录一次登录失败（密码错误、账号不存在等），风险等级固定为 medium。

    调用约束：由登录链路在认证失败后调用；本函数只 add 事件，不提交也不抛业务异常，
    真正的拒绝提示由登录链路决定，避免通过“事件记录失败”泄露账号是否存在。
    """
    _event(
        db, workspace_id=workspace_id, user_id=user_id, username=username,
        auth_method=auth_method, outcome="failed", reason=reason, risk_level="medium",
        ip_address=client_ip(request), device_hash=hash_device(workspace_id, device_id),
        user_agent_value=user_agent(request),
    )


def evaluate_login(db, *, workspace: Workspace, user_id: str, username: str,
                   auth_method: str, request, device_id: str | None,
                   device_hash_override: str | None = None) -> dict:
    """Apply IP/device/session rules before issuing a token.

    用途：登录（本地账号与 OIDC）发放令牌前的策略判定，是所有安全规则的执行点。
    参数：db 会话；workspace 目标工作区；user_id/username 登录主体；auth_method 认证方式
    （local/oidc）；request 原始请求（取 socket IP 与 UA）；device_id 前端设备标识；
    device_hash_override 用于 OIDC —— 跳转前已算好的设备哈希（回调时前端标识不可用）。
    返回：安全上下文字典 {ip_address, user_agent, device_hash, risk_level}，由调用方传入
    record_success 落库；OIDC 场景会在此之后用跳转前的地址/设备覆盖，保持“发起时”的证据。
    异常：AuthError —— 命中阻止网段（blocked_ip）、不在允许网段（ip_not_allowed）、
    需要可信设备但设备未批准（device_approval_required）、会话数达上限且策略为 block（session_limit），
    都带有机器可读的 reason 或提示字段供前端识别。
    权限：无（登录前调用，此时还没有会话）。
    副作用（重要）：
    - 首次出现的设备会以 pending 状态写入 system_trusted_devices（risk=medium）；
    - 被拒绝的分支会先写 LoginEvent 再 commit，确保拒绝记录不被后续异常回滚掉；
    - 会话超限且策略为 revoke_oldest 时，会按 last_seen_at 从旧到新撤销多余会话（不提交，
      由登录链路统一提交）。
    约束：policy.enabled=False 时完全跳过 IP/设备/会话限制（只保留设备登记与事件记录）。
    """
    policy = get_policy(db, workspace.id)
    ip_value = client_ip(request)
    ua = user_agent(request)
    device = device_hash_override if device_hash_override is not None else hash_device(workspace.id, device_id)
    risk = "low"
    if policy.enabled:
        if policy.blocked_cidrs and _ip_matches(ip_value, policy.blocked_cidrs):
            _event(db, workspace_id=workspace.id, user_id=user_id, username=username,
                   auth_method=auth_method, outcome="blocked", reason="blocked_ip", risk_level="high",
                   ip_address=ip_value, device_hash=device, user_agent_value=ua)
            db.commit()
            raise AuthError("当前网络地址不允许登录", login_blocked=True, reason="blocked_ip")
        if policy.allowed_cidrs and not _ip_matches(ip_value, policy.allowed_cidrs):
            _event(db, workspace_id=workspace.id, user_id=user_id, username=username,
                   auth_method=auth_method, outcome="blocked", reason="ip_not_allowed", risk_level="high",
                   ip_address=ip_value, device_hash=device, user_agent_value=ua)
            db.commit()
            raise AuthError("当前网络地址不在登录允许范围", login_blocked=True, reason="ip_not_allowed")
    device_row = None
    if device:
        device_row = db.scalar(select(TrustedDevice).where(
            TrustedDevice.workspace_id == workspace.id,
            TrustedDevice.user_id == user_id, TrustedDevice.device_hash == device,
        ))
        if device_row is None:
            device_row = TrustedDevice(
                id=new_id(), workspace_id=workspace.id, user_id=user_id,
                device_hash=device, label="新设备", status="pending",
                first_seen_at=datetime.utcnow(), last_seen_at=datetime.utcnow(),
            )
            db.add(device_row)
            db.flush()
            risk = "medium"
        else:
            device_row.last_seen_at = datetime.utcnow()
            if device_row.status != "trusted":
                risk = "medium"
    if policy.enabled and policy.require_trusted_device and (device_row is None or device_row.status != "trusted"):
        _event(db, workspace_id=workspace.id, user_id=user_id, username=username,
               auth_method=auth_method, outcome="blocked", reason="device_approval_required", risk_level="high",
               ip_address=ip_value, device_hash=device, user_agent_value=ua,
               details={"device_id": device_row.id if device_row else None})
        db.commit()
        raise AuthError("此设备需要管理员批准后才能登录", device_approval_required=True,
                        device_request_id=device_row.id if device_row else None)
    active_sessions = db.execute(select(SessionToken, SessionContext).join(
        SessionContext, SessionContext.session_id == SessionToken.id,
    ).where(
        SessionContext.workspace_id == workspace.id, SessionToken.user_id == user_id,
        SessionToken.revoked_at.is_(None), SessionToken.expires_at > datetime.utcnow(),
    ).order_by(SessionToken.last_seen_at, SessionToken.id)).all()
    if policy.enabled and len(active_sessions) >= policy.max_active_sessions:
        if policy.session_overflow == "block":
            _event(db, workspace_id=workspace.id, user_id=user_id, username=username,
                   auth_method=auth_method, outcome="blocked", reason="session_limit", risk_level="medium",
                   ip_address=ip_value, device_hash=device, user_agent_value=ua)
            db.commit()
            raise AuthError("有效会话数量已达到管理员设置的上限", login_blocked=True, reason="session_limit")
        revoke_count = len(active_sessions) - policy.max_active_sessions + 1
        for session, _ in active_sessions[:revoke_count]:
            session.revoked_at = datetime.utcnow()
    return {"ip_address": ip_value, "user_agent": ua, "device_hash": device, "risk_level": risk}


def record_success(db, *, workspace_id: str, session_id: str, user_id: str,
                   username: str, auth_method: str, security_context: dict) -> None:
    """记录登录成功：写入会话安全上下文（SessionContext）并追加一条 success 事件。

    参数：security_context 必须来自 evaluate_login 的返回值（键固定为 ip_address/user_agent/
    device_hash/risk_level），缺键会直接 KeyError，因此不要手工拼装。
    副作用：add 两行记录，不提交；调用方需保证会话行已 flush（session_id 需已存在）。
    """
    db.add(SessionContext(
        id=new_id(), workspace_id=workspace_id, session_id=session_id, user_id=user_id,
        auth_method=auth_method, ip_address=security_context["ip_address"],
        device_hash=security_context["device_hash"], user_agent=security_context["user_agent"],
        risk_level=security_context["risk_level"],
    ))
    _event(db, workspace_id=workspace_id, user_id=user_id, username=username,
           auth_method=auth_method, outcome="success", reason="authenticated",
           risk_level=security_context["risk_level"], ip_address=security_context["ip_address"],
           device_hash=security_context["device_hash"], user_agent_value=security_context["user_agent"])


def list_devices(ctx: AuthContext, page: int, page_size: int, status: str = "") -> dict:
    """分页查看可信设备与待审批设备（GET /api/v1/system/security/devices，需要 system.security.view）。

    参数：status 可选，只接受 pending/trusted/revoked，其它值报 ValidationError。
    返回：分页结构；device_hash 截断为前 12 位展示，避免完整哈希被用于跨库匹配。
    异常：PermissionErrorEx（缺少查看权限）；ValidationError（筛选取值非法）。
    副作用：只读。注意设备是全工作区共享的审批列表，受 system.security.view 这一个动作控制，
    不按人员数据范围裁剪（管理员需要看到所有待审批设备）。
    """
    require_permission(ctx, "system.security.view")
    query = select(TrustedDevice).where(TrustedDevice.workspace_id == ctx.workspace_id)
    if status:
        if status not in {"pending", "trusted", "revoked"}:
            raise ValidationError("设备状态筛选值无效")
        query = query.where(TrustedDevice.status == status)
    total = ctx.db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = ctx.db.scalars(query.order_by(TrustedDevice.last_seen_at.desc(), TrustedDevice.id)
                          .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{
        "id": row.id, "user_id": row.user_id, "device_hash": row.device_hash[:12],
        "label": row.label, "status": row.status, "version": row.version,
        "first_seen_at": row.first_seen_at, "last_seen_at": row.last_seen_at,
        "approved_by": row.approved_by, "approved_at": row.approved_at,
    } for row in rows], "total": total, "page": page, "page_size": page_size}


def decide_device(ctx: AuthContext, device_id: str, payload: DeviceDecision) -> dict:
    """批准或撤销一个设备（PATCH /api/v1/system/security/devices/{id}，需要 system.security.manage）。

    参数：device_id 设备记录标识；payload 为 DeviceDecision（version 必填，status 只允许
    trusted/revoked，label 可选为新设备命名）。
    返回：{id, status, label, version, approved_at, revoked_at}。
    异常：NotFoundError（设备不属于本工作区）；ConflictError（版本冲突）；PermissionErrorEx（缺权限）。
    副作用：改写设备状态与审批信息（trusted 记录 approved_by/approved_at 并清空 revoked_at；
    revoked 记录 revoked_at），写 security.device_decision 审计并提交。撤销设备会立即影响后续登录
    （require_trusted_device 开启时该设备将无法登录），但不影响该设备上已签发的会话。
    """
    require_permission(ctx, "system.security.manage")
    row = ctx.db.scalar(select(TrustedDevice).where(
        TrustedDevice.id == device_id, TrustedDevice.workspace_id == ctx.workspace_id,
    ))
    if row is None:
        raise NotFoundError("设备申请不存在")
    check_version(row, payload.version)
    row.status = payload.status
    if payload.label is not None:
        row.label = payload.label.strip()
    if payload.status == "trusted":
        row.approved_by = ctx.user_id
        row.approved_at = datetime.utcnow()
        row.revoked_at = None
    else:
        row.revoked_at = datetime.utcnow()
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="security.device_decision", resource_type="trusted_device", resource_id=row.id,
          metadata={"status": row.status})
    ctx.db.commit()
    return {"id": row.id, "status": row.status, "label": row.label, "version": row.version,
            "approved_at": row.approved_at, "revoked_at": row.revoked_at}


def list_login_events(ctx: AuthContext, page: int, page_size: int, outcome: str = "") -> dict:
    """分页查看登录事件（GET /api/v1/system/security/login-events，需要 system.security.view）。

    参数：outcome 可选，只接受 success/failed/blocked，其它值报 ValidationError。
    返回：分页结构；device_hash 截断为 12 位；reason 是机器码，前端负责映射为中文说明。
    异常：PermissionErrorEx（缺少查看权限）；ValidationError（筛选取值非法）。
    副作用：只读；按创建时间倒序（id 兜底），便于排查最近的异常登录。
    """
    require_permission(ctx, "system.security.view")
    query = select(LoginEvent).where(LoginEvent.workspace_id == ctx.workspace_id)
    if outcome:
        if outcome not in {"success", "failed", "blocked"}:
            raise ValidationError("登录结果筛选值无效")
        query = query.where(LoginEvent.outcome == outcome)
    total = ctx.db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = ctx.db.scalars(query.order_by(LoginEvent.created_at.desc(), LoginEvent.id)
                          .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{
        "id": row.id, "user_id": row.user_id, "username": row.username,
        "auth_method": row.auth_method, "outcome": row.outcome,
        "reason": row.reason, "risk_level": row.risk_level,
        "ip_address": row.ip_address, "device_hash": row.device_hash[:12],
        "user_agent": row.user_agent, "created_at": row.created_at,
    } for row in rows], "total": total, "page": page, "page_size": page_size}
