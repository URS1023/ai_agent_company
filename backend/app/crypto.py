# -*- coding: utf-8 -*-
"""AES-256-GCM envelope encryption for stored secrets (uses SECRET_MASTER_KEY).

职责：对需要落库保存的第三方凭据/密钥做对称加密与解密（如数据源口令、供应商 API Key）。

系统位置：被数据源、模型供应商等模块调用；密钥来自 app.config.settings.SECRET_MASTER_KEY，
解密发生在使用凭据的瞬间，明文不得写库或落日志。

关键约束：
- 采用 AES-256-GCM（带认证的加密），密钥由主密钥经 SHA-256 派生，`key_version` 参与派生，
  为将来轮换主密钥留出兼容位；密文一律以 base64 文本存储，且 nonce 必须与密文成对保存。
- nonce 每次加密随机生成、绝不复用；GCM 的附加认证数据（AAD）当前为 None，解密方必须
  与加密方保持一致，改为携带 AAD 会导致历史密文无法解密。
- 主密钥沿用默认值时"加密"不具备真实防护力，生产必须覆盖 SECRET_MASTER_KEY；
  换钥后历史密文需要用旧 key_version 解出并重新加密写入。
"""
import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings

_CUR_KEY_VERSION = 1


def _derive_key(secret: str, key_version: int) -> bytes:
    """从主密钥与版本号派生出 32 字节 AES 密钥（SHA-256(secret::v<version>)）。

    参数：secret 为 SECRET_MASTER_KEY 的当前值；key_version 为密文记录中的版本号。
    返回：AESGCM 所需的 32 字节密钥。约束：派生公式与版本拼接方式属于存储契约的一部分，
    调整会让已有密文无法解密；轮换密钥请只提升 _CUR_KEY_VERSION 并保留旧密文记录。
    """
    # Keyed by version so we can rotate master keys in the future.
    # 派生方式写死在代码里：改动会使已存密文全部失效，轮换请只改 key_version。
    material = f"{secret}::v{key_version}".encode("utf-8")
    return hashlib.sha256(material).digest()


def encrypt_secret(plaintext: str) -> tuple[str, str, int]:
    """Return (ciphertext_b64, nonce_b64, key_version).

    参数：plaintext 为待加密明文（UTF-8）。
    返回：base64 密文、base64 nonce、密钥版本三元组；三者必须一起落库，
    缺少任一值（尤其 key_version）都会导致后续无法解密。
    """
    nonce = os.urandom(12)
    key = _derive_key(settings.SECRET_MASTER_KEY, _CUR_KEY_VERSION)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(ct).decode("ascii"), base64.b64encode(nonce).decode("ascii"), _CUR_KEY_VERSION


def decrypt_secret(ciphertext_b64: str, nonce_b64: str, key_version: int) -> str:
    """解密由 encrypt_secret 产出的密文并返回明文。

    参数：ciphertext_b64 / nonce_b64 为存储值；key_version 用记录中保存的版本，
    不要传当前版本，否则主密钥轮换前后的数据无法读取。
    异常：base64 解码失败或 GCM 认证失败（密文被篡改、主密钥不匹配）会抛出
    binascii.Error / cryptography.exceptions.InvalidTag，调用方需按"凭据不可用"处理，
    不要回退到明文或旧密钥重试。
    """
    key = _derive_key(settings.SECRET_MASTER_KEY, key_version)
    ct = base64.b64decode(ciphertext_b64)
    nonce = base64.b64decode(nonce_b64)
    pt = AESGCM(key).decrypt(nonce, ct, None)
    return pt.decode("utf-8")
