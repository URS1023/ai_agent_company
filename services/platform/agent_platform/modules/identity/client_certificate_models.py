"""客户端公钥证书、断言重放账本及令牌关联；不保存调用方私钥。"""

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...core.database import Base
from ...core.models import new_id, now_iso


class ServiceClientCertificate(Base):
    __tablename__ = "service_client_certificates"
    __table_args__ = (UniqueConstraint("identity_id", "fingerprint_sha256", name="uq_client_certificate_fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("service_identities.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    certificate_pem: Mapped[str] = mapped_column(Text)
    fingerprint_sha256: Mapped[str] = mapped_column(String(64))
    subject_text: Mapped[str] = mapped_column(Text)
    issuer_text: Mapped[str] = mapped_column(Text)
    serial_number: Mapped[str] = mapped_column(String(80))
    algorithm: Mapped[str] = mapped_column(String(12))
    not_before: Mapped[str] = mapped_column(String(40))
    not_after: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revocation_reason: Mapped[str] = mapped_column(String(500), default="")
    replaced_by_id: Mapped[str | None] = mapped_column(ForeignKey("service_client_certificates.id"), nullable=True)
    revision: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class ServiceClientAssertion(Base):
    __tablename__ = "service_client_assertions"
    __table_args__ = (UniqueConstraint("identity_id", "jti_sha256", name="uq_client_assertion_replay"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("service_identities.id"), index=True)
    certificate_id: Mapped[str] = mapped_column(ForeignKey("service_client_certificates.id"))
    jti_sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class ServiceCertificateTokenBinding(Base):
    __tablename__ = "service_certificate_token_bindings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    certificate_id: Mapped[str] = mapped_column(ForeignKey("service_client_certificates.id"), index=True)
    service_access_id: Mapped[str] = mapped_column(ForeignKey("service_access_tokens.id"), unique=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
