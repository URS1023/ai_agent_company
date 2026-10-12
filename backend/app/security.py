# -*- coding: utf-8 -*-
"""Password hashing (Argon2id) and session token digest helpers.

职责：口令的单向哈希与校验、以及会话令牌的生成与摘要计算。

系统位置：认证模块（登录/改密/bootstrap 建管理员）调用 hash_password / verify_password；
会话创建与鉴权走 generate_session_token，数据库只保存摘要，原始令牌只出现在客户端。

关键约束：
- 口令使用 Argon2id（time_cost=3, memory_cost=64MiB, parallelism=4），参数变更后旧哈希仍可
  校验（参数内嵌在哈希串中），但不要把参数调低；明文口令绝不允许落库或写日志。
- 会话令牌为 32 字节 URL 安全随机串，落库前统一做 SHA-256 摘要；这里的摘要不是口令哈希，
  高熵随机串无需加盐，但摘要算法必须与鉴权查询侧保持一致，改动等于让全部会话失效。
"""
from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

# 哈希参数与口令强度成本绑定：调高只影响新哈希，调低会削弱已有账号的防护。
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)


def hash_password(password: str) -> str:
    """返回 Argon2id 哈希串（含算法与参数，可直接入库）。

    参数：password 为明文口令，调用方需自行完成长度/复杂度校验。
    """
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """校验明文口令与已存哈希是否匹配。

    参数：password_hash 为库中存储的完整 Argon2 串，password 为本次输入的明文。
    返回：匹配返回 True；不匹配、哈希格式损坏或算法不可用时一律返回 False（默认拒绝），
    不区分失败原因，避免向调用方泄露账号状态细节。
    """
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def generate_session_token() -> tuple[str, str]:
    """Return (raw_token, sha256_digest). Storage keeps only the digest.

    返回：原始令牌（只回传客户端，禁止落库/日志）与 SHA-256 摘要（用于落库与查询）。
    约束：两个返回值必须一一对应地使用，鉴权时对来访令牌重新取摘要再比对。
    """
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()
