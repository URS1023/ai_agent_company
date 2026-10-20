"""客户端公钥证书管理及RFC7523 JWT客户端认证的令牌端点。"""

from datetime import datetime, timezone
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import Field
from sqlalchemy.orm import Session

from ...core.context import Actor, require_permission
from ...core.database import get_session
from ...core.settings import get_settings
from .client_certificate_crypto import ASSERTION_TYPE, TOKEN_PATH, configured_audience
from .client_certificate_service import (
    ClientAuthenticationError, ClientCertificateService, certificate_dict, exchange_assertion,
)
from .schemas import IdentityInput, RevisionInput

router = APIRouter()


class CertificateCreate(IdentityInput):
    name: str = Field(min_length=1, max_length=120)
    certificate_pem: str = Field(min_length=1, max_length=24000)


class CertificatePatch(RevisionInput):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None


class CertificateRevoke(RevisionInput):
    reason: str = Field(default="", max_length=500)


class CertificateRotate(CertificateCreate):
    revision: int = Field(ge=1)


@router.get('/service-identities/{identity_id}/certificates')
def list_certificates(identity_id: str, actor: Actor = Depends(require_permission('m14.read')),
                      session: Session = Depends(get_session)):
    return ClientCertificateService(session, actor).list(identity_id)


@router.post('/service-identities/{identity_id}/certificates', status_code=201)
def register_certificate(identity_id: str, body: CertificateCreate,
                         actor: Actor = Depends(require_permission('m14.admin')), session: Session = Depends(get_session)):
    return certificate_dict(ClientCertificateService(session, actor).register(identity_id, body.name, body.certificate_pem))


@router.patch('/service-identities/{identity_id}/certificates/{certificate_id}')
def update_certificate(identity_id: str, certificate_id: str, body: CertificatePatch,
                       actor: Actor = Depends(require_permission('m14.admin')), session: Session = Depends(get_session)):
    return certificate_dict(ClientCertificateService(session, actor).change(identity_id, certificate_id, body.revision,
        body.model_dump(exclude_unset=True, exclude={'revision'})))


@router.post('/service-identities/{identity_id}/certificates/{certificate_id}/revocations')
def revoke_certificate(identity_id: str, certificate_id: str, body: CertificateRevoke,
                       actor: Actor = Depends(require_permission('m14.admin')), session: Session = Depends(get_session)):
    return certificate_dict(ClientCertificateService(session, actor).change(identity_id, certificate_id, body.revision,
        {'revoked_at': datetime.now(timezone.utc).isoformat(), 'revocation_reason': body.reason}, 'revoke'))


@router.post('/service-identities/{identity_id}/certificates/{certificate_id}/rotations', status_code=201)
def rotate_certificate(identity_id: str, certificate_id: str, body: CertificateRotate,
                       actor: Actor = Depends(require_permission('m14.admin')), session: Session = Depends(get_session)):
    return ClientCertificateService(session, actor).rotate(identity_id, certificate_id, body.revision, body.name, body.certificate_pem)


@router.get('/service-identities/{identity_id}/certificates/{certificate_id}/public-certificate')
def download_certificate(identity_id: str, certificate_id: str, actor: Actor = Depends(require_permission('m14.read')),
                         session: Session = Depends(get_session)):
    cert = ClientCertificateService(session, actor).get(identity_id, certificate_id)
    return Response(cert.certificate_pem, media_type='application/x-pem-file',
                    headers={'Content-Disposition': f'attachment; filename="client-{cert.id}.pem"'})


@router.get('/service-identities/{identity_id}/client-auth')
def client_auth_info(identity_id: str, request: Request, actor: Actor = Depends(require_permission('m14.read')),
                     session: Session = Depends(get_session)):
    ClientCertificateService(session, actor).identity(identity_id)
    audience = configured_audience(get_settings(request))
    return {'client_id': identity_id, 'audience': audience, 'token_endpoint': audience,
            'grant_type': 'client_credentials', 'client_assertion_type': ASSERTION_TYPE,
            'token_auth_method': 'private_key_jwt', 'assertion_max_seconds': 300, 'token_max_seconds': 600}


@router.post('/client-auth/token')
async def client_token(request: Request, session: Session = Depends(get_session)):
    """只解析有界标准表单；认证错误保持OAuth格式且回滚所有令牌写入。"""
    try:
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/x-www-form-urlencoded':
            raise ClientAuthenticationError('invalid_request', '请使用标准表单提交', 400)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 32768:
                raise ClientAuthenticationError('invalid_request', '认证请求过大', 400)
        try:
            pairs = parse_qsl(raw.decode('utf-8'), keep_blank_values=True, strict_parsing=True,
                              max_num_fields=6, encoding='utf-8', errors='strict')
        except (ValueError, UnicodeError):
            raise ClientAuthenticationError('invalid_request', '认证参数格式无效', 400) from None
        fields = dict(pairs)
        allowed = {'grant_type', 'client_id', 'client_assertion_type', 'client_assertion', 'scope'}
        if len(fields) != len(pairs) or set(fields) - allowed or not 1 <= len(fields.get('client_id', '')) <= 36:
            raise ClientAuthenticationError('invalid_request', '认证参数格式无效', 400)
        result = exchange_assertion(session, get_settings(request), fields)
        return JSONResponse(result, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})
    except ClientAuthenticationError as exc:
        # 错误响应本身不是异常；显式回滚保证依赖的正常提交不会持久化半次交换。
        session.rollback()
        return JSONResponse({'error': exc.code, 'error_description': exc.message}, status_code=exc.status,
            headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})
