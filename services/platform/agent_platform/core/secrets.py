"""凭据密文存储与轮换；接口只返回引用，不把密钥放进资源JSON或审计。"""

from contextlib import contextmanager
from hashlib import sha256
from datetime import datetime, timedelta, timezone
import hmac
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update

from .errors import DomainError
from .models import SecretEntry, SecretRotationTicket, SecretVersion, now_iso


class SecretVault:
    def __init__(self, database, settings):
        self.database = database
        value = os.environ.get('AGENT_MASTER_KEY', '')
        keyfile = settings.data_dir / 'master.key'
        if not value:
            if settings.environment == 'production':
                raise DomainError(503, 'master_key_missing', '请配置平台凭据加密主密钥')
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            if not keyfile.exists():
                # 排他创建避免并发初始化覆盖已加密数据的主密钥。
                try:
                    with keyfile.open('xb') as f:
                        f.write(Fernet.generate_key())
                    keyfile.chmod(0o600)
                except FileExistsError:
                    pass
            value = keyfile.read_text(encoding='ascii').strip()
        try:
            self.cipher = Fernet(value.encode())
        except (ValueError, TypeError) as exc:
            raise DomainError(503, 'master_key_invalid', '平台加密主密钥格式不正确') from exc
        self.signing_key = sha256(value.encode() + b':file-download-signatures').digest()

    @contextmanager
    def _session(self, existing=None):
        if existing is not None:
            yield existing
        else:
            with self.database.sessions.begin() as session:
                yield session

    @staticmethod
    def metadata(row: SecretEntry):
        return {k: getattr(row, k) for k in ('id', 'name', 'kind', 'revision', 'fingerprint', 'created_at')}

    def put(self, value: str, *, workspace_id: str, actor_id: str, name='凭据', kind='api_key', session=None):
        if not value or len(value) > 65536:
            raise DomainError(422, 'invalid_secret', '请填写有效凭据')
        with self._session(session) as s:
            row = SecretEntry(workspace_id=workspace_id, name=name, kind=kind, created_by=actor_id,
                              encrypted_value=self.cipher.encrypt(value.encode()).decode(),
                              fingerprint=sha256(value.encode()).hexdigest()[:12])
            s.add(row)
            s.flush()
            s.add(SecretVersion(secret_id=row.id, workspace_id=workspace_id, revision=1,
                encrypted_value=row.encrypted_value, fingerprint=row.fingerprint, created_by=actor_id))
            s.flush()
            return self.metadata(row)

    def _get(self, session, ref: str, workspace_id: str):
        row = session.scalar(select(SecretEntry).where(SecretEntry.id == ref,
                             SecretEntry.workspace_id == workspace_id, SecretEntry.deleted.is_(False)))
        if row is None:
            raise DomainError(404, 'secret_not_found', '凭据引用不存在或已撤销')
        return row

    def get(self, ref: str, workspace_id: str, session=None, *, revision: int | None = None) -> str:
        with self._session(session) as s:
            row = self._get(s, ref, workspace_id)
            ciphertext = row.encrypted_value
            if revision is not None and revision != row.revision:
                version = s.scalar(select(SecretVersion).where(SecretVersion.secret_id == ref,
                    SecretVersion.workspace_id == workspace_id, SecretVersion.revision == revision))
                if version is None:
                    raise DomainError(404, 'secret_version_not_found', '凭据版本不存在')
                ciphertext = version.encrypted_value
            try:
                return self.cipher.decrypt(ciphertext.encode()).decode()
            except InvalidToken as exc:
                raise DomainError(503, 'secret_decryption_failed', '凭据无法解密，请核对主密钥或重新设置') from exc

    def list(self, workspace_id: str, session=None):
        with self._session(session) as s:
            return [self.metadata(r) for r in s.scalars(select(SecretEntry).where(
                SecretEntry.workspace_id == workspace_id, SecretEntry.deleted.is_(False)))]

    def rotate(self, ref, value, workspace_id, actor_id, expected_revision, session=None):
        with self._session(session) as s:
            row = self._get(s, ref, workspace_id)
            if row.revision != expected_revision:
                raise DomainError(409, 'revision_conflict', '凭据已轮换，请刷新后重试')
            if not value or len(value) > 65536:
                raise DomainError(422, 'invalid_secret', '请填写有效凭据')
            # 为升级前已创建凭据补当前历史快照；旧密文保持原样，不做覆盖更新。
            if s.scalar(select(SecretVersion.id).where(SecretVersion.secret_id == ref,
                    SecretVersion.revision == row.revision)) is None:
                s.add(SecretVersion(secret_id=ref, workspace_id=workspace_id, revision=row.revision,
                    encrypted_value=row.encrypted_value, fingerprint=row.fingerprint,
                    created_by=row.created_by, created_at=row.created_at))
            ciphertext = self.cipher.encrypt(value.encode()).decode()
            fingerprint = sha256(value.encode()).hexdigest()[:12]
            changed = s.execute(update(SecretEntry).where(SecretEntry.id == ref,
                SecretEntry.workspace_id == workspace_id, SecretEntry.deleted.is_(False),
                SecretEntry.revision == expected_revision).values(encrypted_value=ciphertext,
                    fingerprint=fingerprint, revision=expected_revision + 1),
                execution_options={'synchronize_session': False})
            if changed.rowcount != 1:
                raise DomainError(409, 'revision_conflict', '凭据已轮换，请刷新后重试')
            s.add(SecretVersion(secret_id=ref, workspace_id=workspace_id, revision=expected_revision + 1,
                encrypted_value=ciphertext, fingerprint=fingerprint, created_by=actor_id))
            s.flush()
            s.refresh(row)
            return self.metadata(row)

    def rotation_ticket(self, ref, value, workspace_id, actor_id, expected_revision, *, ttl_seconds=120, session=None):
        """签发绑定凭据、用户和工作区的短期票据，明文新值只进入加密字段。"""
        if not value or len(value) > 65536 or not 30 <= ttl_seconds <= 300:
            raise DomainError(422, 'invalid_rotation_request', '请填写有效的新凭据和轮换时效')
        with self._session(session) as s:
            row = self._get(s, ref, workspace_id)
            if row.revision != expected_revision:
                raise DomainError(409, 'revision_conflict', '凭据已轮换，请刷新后重试')
            ticket = 'rotate_' + secrets.token_urlsafe(40)
            expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
            s.add(SecretRotationTicket(token_hash=sha256(ticket.encode()).hexdigest(), secret_id=ref,
                workspace_id=workspace_id, actor_id=actor_id, expected_revision=expected_revision,
                encrypted_value=self.cipher.encrypt(value.encode()).decode(), expires_at=expires))
            s.flush()
            return {'vault_import_ticket': ticket, 'expires_at': expires, 'secret_id': ref,
                    'revision': expected_revision}

    def consume_rotation(self, ref, ticket, workspace_id, actor_id, *, session=None):
        """同事务CAS消费票据及轮换；过期、换人、换工作区和重复使用均拒绝。"""
        with self._session(session) as s:
            token_hash = sha256(ticket.encode()).hexdigest()
            row = s.get(SecretRotationTicket, token_hash)
            if row is None or row.secret_id != ref or row.workspace_id != workspace_id or row.actor_id != actor_id:
                raise DomainError(403, 'rotation_ticket_denied', '轮换票据无效或不属于当前操作')
            if row.consumed_at:
                raise DomainError(409, 'rotation_ticket_used', '轮换票据已使用')
            if datetime.fromisoformat(row.expires_at) <= datetime.now(timezone.utc):
                raise DomainError(410, 'rotation_ticket_expired', '轮换票据已过期，请重新提交')
            changed = s.execute(update(SecretRotationTicket).where(
                SecretRotationTicket.token_hash == token_hash, SecretRotationTicket.consumed_at.is_(None),
                SecretRotationTicket.expires_at > now_iso()).values(consumed_at=now_iso()),
                execution_options={'synchronize_session': False})
            if changed.rowcount != 1:
                raise DomainError(409, 'rotation_ticket_used', '轮换票据已使用或过期')
            try:
                value = self.cipher.decrypt(row.encrypted_value.encode()).decode()
            except InvalidToken as exc:
                raise DomainError(503, 'secret_decryption_failed', '轮换票据无法解密，请重新创建') from exc
            result = self.rotate(ref, value, workspace_id, actor_id, row.expected_revision, session=s)
            s.expire(row)
            return result

    def delete(self, ref, workspace_id, actor_id, session=None):
        with self._session(session) as s:
            row = self._get(s, ref, workspace_id)
            changed = s.execute(update(SecretEntry).where(SecretEntry.id == ref,
                SecretEntry.workspace_id == workspace_id, SecretEntry.deleted.is_(False),
                SecretEntry.revision == row.revision).values(deleted=True, revision=row.revision + 1),
                execution_options={'synchronize_session': False})
            if changed.rowcount != 1:
                raise DomainError(409, 'revision_conflict', '凭据已更新，请刷新后重试')
            s.flush()
            s.expire(row)

    def sign(self, message: str) -> str:
        return hmac.new(self.signing_key, message.encode(), 'sha256').hexdigest()
