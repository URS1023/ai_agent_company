# -*- coding: utf-8 -*-
"""Local username/password authentication.

本地账号（provider=``local``）的登录、登出与改密服务层，被 ``auth.router`` 调用，也是
其他登录方式（如 SSO）复用会话签发/工作区解析逻辑的参考实现。

依赖：``models`` 中的 AuthIdentity / LocalCredential / SessionToken / User / Workspace /
WorkspaceMember，``security`` 提供口令哈希与会话令牌生成，``config.settings`` 提供
SESSION_TTL_SECONDS / IDLE_TTL_SECONDS；登录风控与审计分别走
``modules.system.security_service`` 与 ``common.audit.audit``。

关键约束：
- 用户名一律经 ``_normalize_username`` 归一化后比对，不能直接用原始输入查询。
- 只有「生命周期与组织状态均为 active」的工作区才允许登录，见 ``_active_workspace``。
- 会话表只存 ``token_digest``（SHA-256），明文令牌只在登录响应里返回一次。
- 改密会递增 ``credential_version`` 并吊销该用户全部历史会话，属于不可回滚的安全操作。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import settings
from ...errors import AuthError, ValidationError
from ...ids import new_id
from ...models import AuthIdentity, LocalCredential, SessionToken, User, Workspace, WorkspaceMember
from ...security import generate_session_token, hash_password, verify_password


def _normalize_username(username: str) -> str:
    """归一化用户名（去空白 + 转小写），用于唯一性比对。

    数据库存的是归一化后的 ``username_normalized``，因此任何按用户名查找都必须先过这里，
    否则大小写或首尾空格差异会导致「查不到账号」。
    """
    return username.strip().lower()


def _find_identity(db: Session, username: str):
    """按归一化用户名查找本地登录身份，找不到返回 None。

    仅匹配 ``provider == "local"``，第三方身份不参与本地口令登录。
    """
    n = _normalize_username(username)
    return db.execute(
        select(AuthIdentity).where(
            AuthIdentity.provider == "local", AuthIdentity.username_normalized == n
        )
    ).scalar_one_or_none()


def _find_credential(db: Session, identity_id: str):
    """读取某个登录身份对应的本地口令记录，不存在表示该账号尚未设置密码。"""
    return db.execute(
        select(LocalCredential).where(LocalCredential.auth_identity_id == identity_id)
    ).scalar_one_or_none()


def _active_workspace(db: Session, user_id: str, requested: str | None) -> Workspace:
    """挑选本次登录要落到哪个工作区。

    参数：
        user_id: 目标用户。
        requested: 前端指定的工作区 id；为 None 时按成员创建时间取第一个可用工作区。

    返回：
        可登录的 ``Workspace`` 实例。

    业务异常：
        ``AuthError``：该用户在所有（或指定的）工作区里都没有可用归属时抛出。

    约束：工作区可用性由 ``WorkspaceLifecycle.status`` 与 ``OrganizationProfile.status``
    共同决定，任一为「非 active」即视为不可登录；缺少对应记录时按「未启用治理，放行」处理，
    以兼容历史数据。
    """
    memberships = db.scalars(select(WorkspaceMember).where(
        WorkspaceMember.user_id == user_id,
    ).order_by(WorkspaceMember.created_at, WorkspaceMember.id)).all()
    if requested:
        memberships = [item for item in memberships if item.workspace_id == requested]
    for membership in memberships:
        workspace = db.get(Workspace, membership.workspace_id)
        if workspace is None:
            continue
        from app.modules.system.models import OrganizationProfile, WorkspaceLifecycle
        lifecycle = db.scalar(select(WorkspaceLifecycle).where(
            WorkspaceLifecycle.workspace_id == workspace.id,
        ))
        organization = db.scalar(select(OrganizationProfile).where(
            OrganizationProfile.organization_id == workspace.organization_id,
        ))
        if (lifecycle is None or lifecycle.status == "active") and (
            organization is None or organization.status == "active"
        ):
            return workspace
    raise AuthError("账号没有可登录的工作区")


def login(db: Session, username: str, password: str, *, request,
          workspace_id: str | None = None, device_id: str | None = None) -> dict:
    """本地账号口令登录：校验凭据、评估风控、签发会话并写审计。

    参数：
        db: 数据库会话（由调用方负责提交/回滚之外的生命周期）。
        username: 用户输入的用户名，内部会归一化。
        password: 明文口令，仅在本次校验中使用，不落库。
        request: FastAPI 请求对象，用于风控记录来源 IP/UA。
        workspace_id: 可选，指定登录工作区。
        device_id: 可选，前端设备标识，用于登录风控与审计。

    返回：
        ``_build_login_payload`` 生成的登录载荷，含明文 ``token``（仅此一次返回）、
        用户信息、可访问工作区列表与过期时间。

    业务异常：
        ``AuthError``：用户名/口令错误、账号被禁用、未设置密码、账号临时锁定、
        临时密码过期、无可用工作区。失败分支都会先写一条失败登录记录再提交，
        因此调用方不需要（也不应该）再补记录。

    副作用：写入失败登录记录、成功登录记录、``auth.login`` 审计，并新增 SessionToken；
    所有写入在本函数内 ``db.commit()``。
    """
    identity = _find_identity(db, username)
    if not identity:
        raise AuthError("用户名或密码错误")
    user = db.get(User, identity.user_id)
    # 工作区必须先于账号状态解析：下面的每条失败登录记录都要求 workspace_id；
    # 若该账号没有任何可用工作区，会先在这里抛错，而不是走到后面的禁用分支。
    workspace = _active_workspace(db, identity.user_id, workspace_id)
    from app.modules.system import security_service as login_security
    if not user or user.status != "active":
        login_security.record_failed_login(
            db, workspace_id=workspace.id, user_id=identity.user_id, username=username,
            auth_method="local", request=request, device_id=device_id, reason="account_disabled",
        )
        db.commit()
        raise AuthError("账号不可用，请联系管理员")
    cred = _find_credential(db, identity.id)
    if not cred:
        raise AuthError("账号未设置密码")
    now = datetime.utcnow()
    if cred.locked_until and cred.locked_until > now:
        login_security.record_failed_login(
            db, workspace_id=workspace.id, user_id=user.id, username=username,
            auth_method="local", request=request, device_id=device_id, reason="account_locked",
        )
        db.commit()
        raise AuthError("账号已被临时锁定，请稍后再试")
    if cred.temporary_password_expires_at and cred.temporary_password_expires_at < now:
        login_security.record_failed_login(
            db, workspace_id=workspace.id, user_id=user.id, username=username,
            auth_method="local", request=request, device_id=device_id, reason="temporary_password_expired",
        )
        db.commit()
        raise AuthError("临时密码已过期，请联系管理员")
    if not verify_password(cred.password_hash, password):
        policy = login_security.get_policy(db, workspace.id)
        cred.failed_attempt_count = (cred.failed_attempt_count or 0) + 1
        if cred.failed_attempt_count >= policy.max_failed_attempts:
            cred.locked_until = now + timedelta(minutes=policy.lock_minutes)
            cred.failed_attempt_count = 0
        login_security.record_failed_login(
            db, workspace_id=workspace.id, user_id=user.id, username=username,
            auth_method="local", request=request, device_id=device_id, reason="invalid_credentials",
        )
        db.commit()
        raise AuthError("用户名或密码错误")
    cred.failed_attempt_count = 0
    cred.locked_until = None
    security_context = login_security.evaluate_login(
        db, workspace=workspace, user_id=user.id, username=identity.username_normalized,
        auth_method="local", request=request, device_id=device_id,
    )
    raw_token, digest = generate_session_token()
    session = SessionToken(
        id=new_id(),
        user_id=identity.user_id,
        token_digest=digest,
        credential_version=cred.credential_version,
        expires_at=now + timedelta(seconds=settings.SESSION_TTL_SECONDS),
        idle_expires_at=now + timedelta(seconds=settings.IDLE_TTL_SECONDS),
        last_seen_at=now,
    )
    db.add(session)
    db.flush()
    login_security.record_success(
        db, workspace_id=workspace.id, session_id=session.id, user_id=user.id,
        username=identity.username_normalized, auth_method="local",
        security_context=security_context,
    )
    from app.common.audit import audit
    # 防御性判空：_active_workspace 无可用工作区时抛异常而非返回 None，正常路径必有值。
    if workspace:
        audit(db, workspace_id=workspace.id, actor_user_id=user.id,
              action="auth.login", resource_type="user", resource_id=user.id)
    db.commit()
    return _build_login_payload(
        db, identity.user_id, raw_token, preferred_identity=identity,
        preferred_workspace_id=workspace.id,
    )


def _build_login_payload(db: Session, user_id: str, raw_token: str, *,
                         preferred_identity: AuthIdentity | None = None,
                         preferred_workspace_id: str | None = None,
                         username_override: str | None = None) -> dict:
    """组装登录/切换身份后的响应载荷（不含任何写操作）。

    参数：
        raw_token: 已生成的明文会话令牌，仅在响应中出现一次。
        preferred_identity: 已知的登录身份，避免重复查询。
        preferred_workspace_id: 期望排在列表首位的工作区。
        username_override: 覆盖展示用用户名（例如 SSO 场景显示外部账号名）。

    返回：
        含 ``token`` / ``user`` / ``workspaces`` / ``expire_at`` 的字典；
        ``user.must_change_password`` 由本地口令记录决定，前端据此强制跳转改密页。

    约束：``workspaces`` 已过滤掉生命周期或组织状态非 active 的工作区，与
    ``_active_workspace`` 的判定口径必须保持一致，否则会出现「能登录但列表里没有」。
    """
    user = db.get(User, user_id)
    # 无已知身份时优先取 local 身份，用于展示本地用户名；router.me 未做同样的排序，
    # 两处口径不同，改动时需一并评估。
    identity = preferred_identity or db.execute(
        select(AuthIdentity).where(AuthIdentity.user_id == user_id).order_by(
            (AuthIdentity.provider == "local").desc(), AuthIdentity.created_at,
        )
    ).scalars().first()
    memberships = db.execute(
        select(WorkspaceMember, Workspace)
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(WorkspaceMember.created_at)
    ).all()
    active_memberships = []
    from app.modules.system.models import OrganizationProfile, WorkspaceLifecycle
    for wm, ws in memberships:
        lifecycle = db.scalar(select(WorkspaceLifecycle).where(WorkspaceLifecycle.workspace_id == ws.id))
        organization = db.scalar(select(OrganizationProfile).where(
            OrganizationProfile.organization_id == ws.organization_id,
        ))
        if (lifecycle is None or lifecycle.status == "active") and (
            organization is None or organization.status == "active"
        ):
            active_memberships.append((wm, ws))
    # 借助 False < True 把指定工作区排到列表首位；sort 稳定，其余成员仍保持上面 created_at 的顺序。
    active_memberships.sort(key=lambda item: item[1].id != preferred_workspace_id)
    workspaces = [{"id": wm.workspace_id, "organization_id": ws.organization_id,
                   "name": ws.name, "role": wm.role} for wm, ws in active_memberships]
    local_identity = db.scalar(select(AuthIdentity).where(
        AuthIdentity.user_id == user_id, AuthIdentity.provider == "local",
    ))
    credential = _find_credential(db, local_identity.id) if local_identity else None
    return {
        "token": raw_token,
        "user": {
            "id": user.id,
            "display_name": user.display_name,
            "username": username_override or (identity.username_normalized if identity else ""),
            "status": user.status,
            "must_change_password": bool(credential and credential.must_change_password),
        },
        "workspaces": workspaces,
        "expire_at": (datetime.utcnow() + timedelta(seconds=settings.SESSION_TTL_SECONDS)).isoformat(),
    }


def logout(db: Session, token: str) -> None:
    """按摘要撤销当前用户会话或服务令牌，不影响同用户/客户端的其他令牌。

    参数：
        token: 当前请求的明文令牌；只用 SHA-256 摘要定位对应记录。

    返回：无。

    约束：服务层对不存在或已撤销的令牌幂等，已撤销令牌的 HTTP 请求仍由鉴权拒绝。
    副作用：仅更新匹配的 SessionToken 或 IntegrationAccessToken.revoked_at 并提交。
    """
    import hashlib
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = db.execute(select(SessionToken).where(SessionToken.token_digest == digest)).scalar_one_or_none()
    if row is None:
        from ..system_tools.models import IntegrationAccessToken

        # 客户端停用/轮换会批量撤销令牌；退出只能撤销本次传入的令牌，不能复用批量路径。
        row = db.scalar(select(IntegrationAccessToken).where(IntegrationAccessToken.token_digest == digest))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        db.commit()


def change_password(db: Session, user_id: str, old_password: str, new_password: str) -> None:
    """Replace a verified password and revoke every prior session immediately.

    校验旧口令后替换为新口令，并立即吊销该用户全部历史会话。

    参数：
        user_id: 目标用户；口令记录从该用户的 local 身份上取。
        old_password: 当前口令，必须校验通过。
        new_password: 新口令，需满足长度 ≥ 10 且同时含字母与数字。

    返回：无。

    业务异常：
        ``AuthError``：旧口令不正确或账号没有本地口令记录。
        ``ValidationError``：新旧口令相同，或新口令强度不足。

    副作用：更新口令哈希、递增 ``credential_version``、清除 must_change_password /
    临时密码有效期 / 失败计数 / 锁定状态，吊销全部会话，并为该用户的每个工作区各写一条
    ``auth.password_change`` 审计；最后统一提交。任何一处失败都不应留下部分写入，
    调用方不要在此函数外重复提交。
    """
    identity = db.scalar(select(AuthIdentity).where(
        AuthIdentity.user_id == user_id, AuthIdentity.provider == "local",
    ))
    credential = _find_credential(db, identity.id) if identity else None
    if credential is None or not verify_password(credential.password_hash, old_password):
        raise AuthError("当前密码不正确")
    if old_password == new_password:
        raise ValidationError("新密码不能与当前密码相同")
    if len(new_password) < 10 or not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):
        raise ValidationError("新密码至少 10 位，并同时包含字母和数字")
    credential.password_hash = hash_password(new_password)
    credential.credential_version += 1
    credential.must_change_password = False
    credential.temporary_password_expires_at = None
    credential.password_changed_at = datetime.utcnow()
    credential.failed_attempt_count = 0
    credential.locked_until = None
    for session in db.scalars(select(SessionToken).where(SessionToken.user_id == user_id)).all():
        session.revoked_at = datetime.utcnow()
    from app.common.audit import audit
    for membership in db.scalars(select(WorkspaceMember).where(WorkspaceMember.user_id == user_id)).all():
        audit(db, workspace_id=membership.workspace_id, actor_user_id=user_id,
              action="auth.password_change", resource_type="user", resource_id=user_id)
    db.commit()
