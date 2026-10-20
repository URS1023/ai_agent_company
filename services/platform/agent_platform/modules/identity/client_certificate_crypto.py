"""采用X.509解析与PyJWT验签；证书由管理员显式登记信任，不自动联网建立CA信任。"""

from datetime import datetime, timezone
import ipaddress
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
import jwt

from ...core.errors import DomainError

ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
TOKEN_PATH = "/api/v1/client-auth/token"


def configured_audience(settings) -> str:
    """接收方只来自固定服务器配置；外部HTTP、用户信息及动态Host不能成为信任依据。"""
    value = settings.client_assertion_audience
    try:
        parts = urlsplit(value)
        host = parts.hostname
        local = host == "localhost"
        if host and not local:
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
        valid = (
            bool(value) and value == value.strip() and len(value) <= 2000
            and parts.scheme in {"https", "http"} and bool(host)
            and (parts.scheme == "https" or local)
            and not parts.username and not parts.password
            and not parts.query and not parts.fragment
            and parts.path == TOKEN_PATH and parts.port != 0
            and not any(char.isspace() for char in value)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise DomainError(503, "client_auth_not_configured", "客户端认证地址配置不可用")
    return value


def parse_public_certificate(value: str) -> dict:
    """只接收单张公钥证书，限制签名算法与密钥强度；原私钥不会进入模型或审计。"""
    try:
        pem = value.strip()
        if (len(pem) > 24000 or "PRIVATE KEY" in pem or pem.count("-----BEGIN CERTIFICATE-----") != 1
                or not pem.startswith("-----BEGIN CERTIFICATE-----")
                or not pem.endswith("-----END CERTIFICATE-----")):
            raise ValueError()
        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
        # 重新编码后核对，拒绝夹带另一段PEM或证书外任意数据。
        canonical = cert.public_bytes(serialization.Encoding.PEM).decode()
        if "".join(pem.split()) != "".join(canonical.split()):
            raise ValueError()
        public_key = cert.public_key()
        if isinstance(public_key, rsa.RSAPublicKey) and 2048 <= public_key.key_size <= 8192:
            algorithm = "RS256"
        elif isinstance(public_key, ec.EllipticCurvePublicKey) and isinstance(public_key.curve, ec.SECP256R1):
            algorithm = "ES256"
        else:
            raise ValueError()
        try:
            if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise ValueError()
        except x509.ExtensionNotFound:
            pass
        try:
            if not cert.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature:
                raise ValueError()
        except x509.ExtensionNotFound:
            pass
        if cert.not_valid_after_utc <= datetime.now(timezone.utc) or cert.not_valid_after_utc <= cert.not_valid_before_utc:
            raise ValueError()
        return {
            "certificate_pem": canonical,
            "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
            "subject_text": cert.subject.rfc4514_string(),
            "issuer_text": cert.issuer.rfc4514_string(),
            "serial_number": str(cert.serial_number), "algorithm": algorithm,
            "not_before": cert.not_valid_before_utc.isoformat(),
            "not_after": cert.not_valid_after_utc.isoformat(),
        }
    except (ValueError, TypeError, UnicodeError, x509.DuplicateExtension, UnsupportedAlgorithm):
        raise DomainError(422, "client_certificate_invalid", "请选择有效的客户端公钥证书", {"certificate_pem": "证书格式、用途、密钥或期限不符合要求"}) from None


def certificate_active(cert, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return bool(cert.enabled and not cert.revoked_at
                and datetime.fromisoformat(cert.not_before) <= now < datetime.fromisoformat(cert.not_after))


def assertion_header(assertion: str) -> dict:
    """仅用未验证kid定位已登记证书；算法允许值始终由登记证书确定。"""
    if not assertion or len(assertion) > 24000:
        raise ValueError()
    header = jwt.get_unverified_header(assertion)
    if (set(header) - {"alg", "kid", "typ"} or header.get("typ", "JWT") != "JWT"
            or not isinstance(header.get("kid"), str) or not 1 <= len(header["kid"]) <= 36):
        raise ValueError()
    return header


def verify_assertion(assertion: str, certificate, identity_id: str, audience: str) -> dict:
    """验签和标准声明验证后，再收紧五分钟窗口及NumericDate类型，避免布尔/字符串时间。"""
    key = x509.load_pem_x509_certificate(certificate.certificate_pem.encode()).public_key()
    claims = jwt.decode(
        assertion, key, algorithms=[certificate.algorithm], audience=audience,
        issuer=identity_id, subject=identity_id, leeway=15,
        options={"require": ["iss", "sub", "aud", "exp", "iat", "nbf", "jti"], "strict_aud": True},
    )
    now = datetime.now(timezone.utc).timestamp()
    if (any(type(claims[name]) is not int for name in ("iat", "nbf", "exp"))
            or not isinstance(claims["jti"], str) or not 1 <= len(claims["jti"]) <= 200
            or not 0 < claims["exp"] - claims["iat"] <= 300
            or not now - 315 <= claims["iat"] <= now + 15
            or not claims["iat"] - 15 <= claims["nbf"] <= min(claims["exp"], now + 15)
            or claims["exp"] <= now):
        raise ValueError()
    # 孤立UTF-16代理项不是合法UTF-8字符串，不能进入重放摘要或数据库。
    claims["jti"].encode("utf-8")
    return claims
