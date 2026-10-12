# -*- coding: utf-8 -*-
"""Secret management: store/retrieve encrypted secrets (AES-GCM), reference-only usage.

密钥加解密服务：写库前用 AES-GCM 加密，读取时按记录内的 ``key_version`` 解密；
上层业务（capabilities、数据源等）只保存 / 传递 ``secret_id`` 引用，从不持有明文。

依赖 ``crypto.encrypt_secret`` / ``crypto.decrypt_secret``，主密钥来自配置，轮换依靠
``key_version`` 列而非重新加密历史数据。

约束：
- 本模块函数只 flush 不 commit（``create_secret`` / ``get_active_secret`` / ``decrypt`` /
  ``decrypt_many``），事务边界由调用方负责；唯一例外是 ``revoke_secret``，它会自行 commit。
- 取用密钥一律要求 ``status == "active"`` 且 ``deleted_at is None``，否则吊销后的密钥仍可解密。
- 本模块不接收 ``AuthContext``，不做工作区隔离与权限校验，调用方必须先自行校验。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...crypto import decrypt_secret, encrypt_secret
from ...errors import NotFoundError
from ...ids import new_id
from ...models import Secret


def create_secret(db: Session, *, workspace_id: str, owner_user_id: str, name: str,
                  value: str, provider: str = "generic") -> Secret:
    """加密并登记一个新密钥（不提交事务）。

    参数：
        db: 调用方持有的会话，事务边界由调用方决定。
        workspace_id / owner_user_id: 归属信息，用于后续工作区隔离与数据范围判定。
        name / provider: 展示名与用途标签（如 api_key / db_password / oauth）。
        value: 明文，仅在本函数内被加密，不会原样落库。

    返回：已 flush 的 ``Secret`` ORM 对象（主键已分配，可立即作为引用 ID 返回）。
    副作用：向会话 add 密文旅并 flush；未 commit，是否落库由调用方决定。
    异常：主密钥缺失或加密失败由 ``crypto.encrypt_secret`` 直接抛出，本函数不做明文降级保存。
    """
    ciphertext, nonce, key_version = encrypt_secret(value)
    row = Secret(
        id=new_id(), workspace_id=workspace_id, owner_user_id=owner_user_id,
        name=name, provider=provider, ciphertext=ciphertext, nonce=nonce,
        key_version=key_version, status="active",
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def get_active_secret(db: Session, secret_id: str) -> Secret | None:
    """按 ID 取"可用"密钥记录，供解密前的可用性判断。

    返回：``Secret`` 或 ``None``；``None`` 覆盖三种情况——记录不存在、已吊销 / 停用、
    已软删除。调用方不应把 ``None`` 当作"存在但暂时不可用"处理。

    注意：本函数不按工作区过滤，跨工作区隔离由调用方（权限层）保证。
    """
    row = db.get(Secret, secret_id)
    if not row or row.status != "active" or row.deleted_at is not None:
        return None
    return row


def decrypt(db: Session, secret_id: str) -> str:
    """取出并解密单个密钥的明文。

    返回：明文字符串，只应在内存中短暂使用，禁止写入日志、审计 metadata 或回显给前端。
    异常：记录不存在、已停用或已删除时抛 ``NotFoundError``；密文损坏、主密钥不匹配时
    由 ``crypto.decrypt_secret`` 抛底层异常，调用方需按"凭据不可用"处理，不要尝试豁免。
    """
    row = get_active_secret(db, secret_id)
    if not row:
        raise NotFoundError("Secret 不存在或已停用")
    return decrypt_secret(row.ciphertext, row.nonce, row.key_version)


def decrypt_many(db: Session, secret_ids: list) -> dict:
    """批量解密，供一次运行中加载多个密钥引用时使用。

    参数：``secret_ids`` 允许包含空值 / 重复值，内部会过滤空值并去重查询。
    返回：``{secret_id: 明文}``，只包含状态 ``active`` 且未删除的记录。
    单条解密失败（密文损坏、密钥版本不匹配）会被跳过而不中断整批，因此调用方必须以
    "键不存在即不可用"处理缺项，不能假定结果与入参一一对应。
    副作用：无（只读，不提交事务）。
    """
    out = {}
    ids = [s for s in secret_ids if s]
    if not ids:
        return out
    rows = db.execute(select(Secret).where(Secret.id.in_(ids), Secret.status == "active",
                                           Secret.deleted_at.is_(None))).scalars().all()
    for row in rows:
        try:
            out[row.id] = decrypt_secret(row.ciphertext, row.nonce, row.key_version)
        except Exception:
            continue
    return out


def revoke_secret(db: Session, secret_id: str) -> None:
    """吊销密钥：状态置为 ``revoked`` 并写软删除时间戳，随后自行提交事务。

    返回：无。记录不存在时静默返回（路由层已先做存在性校验），因此本函数不能用来判断存在性。
    副作用：修改 ``secrets`` 行并 commit，不可恢复；已在第三方系统登记的凭据不会因此失效，
    需要另行在对应平台轮换。审计由调用方负责写入。
    """
    row = db.get(Secret, secret_id)
    if row:
        row.status = "revoked"
        row.deleted_at = datetime.utcnow()
        db.commit()
