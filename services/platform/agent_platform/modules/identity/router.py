"""身份与工作区API；所有修改在服务端再次验证权限及对象归属。"""

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ...core.auth import (DUMMY_PASSWORD_HASH, PASSWORD_HASHER, available_workspaces, me_dict, start_session,
                          verify_password, workspace_dict)
from ...core.context import Actor, get_actor, require_permission, resolve_actor
from ...core.database import get_session
from ...core.errors import DomainError
from ...core.models import (AuditEvent, LoginSession, Membership, Organization, ResourceGrant,
                            Role, SystemState, User, Workspace)
from ...core.records import RecordStore
from ...core.settings import get_settings
from . import schemas as dto
from .models import ServiceAccess, ServiceIdentity
from .security import validate_delegation

router = APIRouter(prefix='/api/v1', tags=['身份与权限'])


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3, max_length=100, pattern=r'^[\w.@-]+$')
    password: str = Field(min_length=1, max_length=256)


class Setup(Credentials):
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)
    workspace_name: str = Field(min_length=1, max_length=160)
    organization_name: str = Field(default='我的企业', min_length=1, max_length=160)

    @field_validator('display_name', 'workspace_name', 'organization_name')
    @classmethod
    def trim_setup_names(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('名称不能为空')
        return value


def set_cookie(response: Response, token: str, request: Request):
    settings = get_settings(request)
    response.set_cookie('agent_session', token, httponly=True, secure=settings.secure_cookies,
                        samesite='lax', max_age=settings.session_hours * 3600, path='/')


@router.get('/auth/status')
def auth_status(session: Session = Depends(get_session)):
    return {'initialized': bool(session.scalar(select(func.count()).select_from(User)))}


@router.post('/auth/setup', status_code=201)
def setup(body: Setup, request: Request, response: Response, session: Session = Depends(get_session)):
    # 进程锁减少本机竞争；SystemState唯一主键承担跨进程首次初始化认领。
    with request.app.state.bootstrap_lock:
        if session.scalar(select(func.count()).select_from(User)):
            raise DomainError(409, 'already_initialized', '系统已初始化，请登录')
        session.add(SystemState(id='initialized', data={'schema_version': 1}))
        session.flush()
        org = Organization(name=body.organization_name.strip())
        session.add(org)
        session.flush()
        workspace = Workspace(organization_id=org.id, name=body.workspace_name.strip())
        session.add(workspace)
        session.flush()
        user = User(organization_id=org.id, username=body.username.casefold(),
                    display_name=body.display_name.strip(), password_hash=PASSWORD_HASHER.hash(body.password), is_admin=True)
        session.add(user)
        session.flush()
        role = Role(workspace_id=workspace.id, name='管理员', permissions=['*'])
        session.add(role)
        session.flush()
        session.add(Membership(workspace_id=workspace.id, user_id=user.id, role_ids=[role.id]))
        login, token = start_session(session, user, get_settings(request))
        actor = Actor(user.id, workspace.id, frozenset({'*'}), ('admin',), org.id, True, login.id)
        RecordStore(session, actor).audit('auth.setup', 'organization', org.id)
        session.commit()
        set_cookie(response, token, request)
        return me_dict(session, login, actor)


@router.post('/auth/login')
def login(body: Credentials, request: Request, response: Response, session: Session = Depends(get_session)):
    key = (request.client.host if request.client else 'unknown') + ':' + body.username.casefold()
    budget = request.app.state.login_budget
    budget.check(key)
    user = session.scalar(select(User).where(User.username == body.username.casefold()))
    service_user = bool(user and session.scalar(select(ServiceIdentity.id).where(ServiceIdentity.user_id == user.id)))
    matched = verify_password(body.password, user.password_hash if user is not None and not service_user else DUMMY_PASSWORD_HASH)
    if user is None or user.disabled or service_user or not matched:
        budget.failure(key)
        raise DomainError(401, 'invalid_credentials', '账号或密码不正确')
    budget.success(key)
    login_row, token = start_session(session, user, get_settings(request))
    actor = resolve_actor(session, login_row)
    RecordStore(session, actor).audit('auth.login', 'user', user.id)
    set_cookie(response, token, request)
    return me_dict(session, login_row, actor)


@router.post('/auth/logout')
def logout(response: Response, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    row = session.get(LoginSession, actor.session_id)
    row.revoked = True
    response.delete_cookie('agent_session', path='/')
    RecordStore(session, actor).audit('auth.logout', 'user', actor.user_id)
    return {'logged_out': True}


@router.get('/me')
def me(actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    return me_dict(session, session.get(LoginSession, actor.session_id), actor)


@router.post('/workspace-selections')
def select_workspace(body: dto.WorkspaceSelection, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    body = body.model_dump()
    user = session.get(User, actor.user_id)
    id = str(body.get('workspace_id', ''))
    if id not in {w.id for w in available_workspaces(session, user)}:
        raise DomainError(403, 'workspace_denied', '当前账号没有该工作区权限')
    login_row = session.get(LoginSession, actor.session_id)
    if session.scalar(select(ServiceAccess.id).where(ServiceAccess.session_id == login_row.id)):
        raise DomainError(403, 'service_workspace_fixed', '服务令牌不能切换工作区')
    # CSRF作为会话选择版本进行CAS，两次并发选择不能悄悄互相覆盖。
    result = session.execute(update(LoginSession).where(LoginSession.id == login_row.id,
        LoginSession.workspace_id == actor.workspace_id, LoginSession.csrf_token == login_row.csrf_token,
        LoginSession.revoked.is_(False)).values(workspace_id=id, csrf_token=secrets.token_urlsafe(32)),
        execution_options={'synchronize_session': False})
    if result.rowcount != 1:
        raise DomainError(409, 'session_selection_conflict', '工作区已切换，请刷新页面')
    session.refresh(login_row)
    RecordStore(session, actor).audit('workspace.select', 'workspace', id)
    session.flush()
    return me_dict(session, login_row)


@router.get('/workspaces')
def workspaces(actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    rows = available_workspaces(session, session.get(User, actor.user_id))
    return {'items': [workspace_dict(w) for w in rows], 'total': len(rows)}


@router.post('/workspaces', status_code=201)
def create_workspace(body: dto.WorkspaceCreate, actor: Actor = Depends(require_permission('m14.admin')),
                     session: Session = Depends(get_session)):
    if not actor.is_admin:
        raise DomainError(403, 'organization_admin_required', '只有企业管理员可以新建工作区')
    body = body.model_dump()
    name = str(body.get('name', '')).strip()
    if not name:
        raise DomainError(422, 'name_required', '请填写工作区名称', {'name': '请输入名称'})
    row = Workspace(organization_id=actor.organization_id, name=name,
                    factory_id=str(body.get('factory_id', '')), industry=str(body.get('industry', '制造业')))
    session.add(row)
    session.flush()
    RecordStore(session, actor).audit('workspace.create', 'workspace', row.id)
    return workspace_dict(row)


@router.patch('/workspaces/{id}')
def update_workspace(id: str, body: dto.WorkspacePatch, actor: Actor = Depends(require_permission('m14.admin')),
                     session: Session = Depends(get_session)):
    body = body.model_dump(exclude_unset=True)
    row = session.scalar(select(Workspace).where(Workspace.id == id, Workspace.organization_id == actor.organization_id))
    if row is None:
        raise DomainError(404, 'workspace_not_found', '工作区不存在')
    if not actor.is_admin and row.id != actor.workspace_id:
        raise DomainError(403, 'workspace_admin_scope', '只能管理当前已授权工作区')
    if row.revision != body.get('revision'):
        raise DomainError(409, 'revision_conflict', '工作区已更新，请刷新后重试')
    if body.get('archived') and row.id == actor.workspace_id:
        raise DomainError(409, 'active_workspace', '请先切换到其他工作区再归档')
    changed = session.execute(update(Workspace).where(Workspace.id == id,
        Workspace.organization_id == actor.organization_id, Workspace.revision == body['revision']).values(
            **{k: v for k, v in body.items() if k != 'revision'}, revision=body['revision'] + 1),
        execution_options={'synchronize_session': False})
    if changed.rowcount != 1:
        raise DomainError(409, 'revision_conflict', '工作区已更新，请刷新后重试')
    session.refresh(row)
    if row.archived:
        session.execute(update(LoginSession).where(LoginSession.workspace_id == id).values(revoked=True))
    RecordStore(session, actor).audit('workspace.update', 'workspace', id)
    return workspace_dict(row)


def role_dict(row: Role):
    return {'id': row.id, 'name': row.name, 'workspace_id': row.workspace_id,
            'permissions': row.permissions, 'revision': row.revision}


@router.get('/roles')
def roles(actor: Actor = Depends(require_permission('m14.read')), session: Session = Depends(get_session)):
    service_roles = select(ServiceIdentity.role_id).where(ServiceIdentity.workspace_id == actor.workspace_id)
    rows = list(session.scalars(select(Role).where(Role.workspace_id == actor.workspace_id, Role.id.not_in(service_roles))))
    return {'items': [role_dict(r) for r in rows], 'total': len(rows)}


@router.post('/roles', status_code=201)
def create_role(body: dto.RoleCreate, actor: Actor = Depends(require_permission('m14.admin')),
                session: Session = Depends(get_session)):
    body = body.model_dump()
    name = str(body.get('name', '')).strip()
    permissions = body.get('permissions', [])
    if not name or not isinstance(permissions, list) or any(not isinstance(p, str) for p in permissions):
        raise DomainError(422, 'invalid_role', '请填写角色名称并选择权限')
    permissions = validate_delegation(actor, permissions)
    row = Role(workspace_id=actor.workspace_id, name=name, permissions=permissions)
    session.add(row)
    session.flush()
    RecordStore(session, actor).audit('role.create', 'role', row.id)
    return role_dict(row)


@router.patch('/roles/{id}')
def patch_role(id: str, body: dto.RolePatch, actor: Actor = Depends(require_permission('m14.admin')),
               session: Session = Depends(get_session)):
    body = body.model_dump(exclude_unset=True)
    row = session.scalar(select(Role).where(Role.id == id, Role.workspace_id == actor.workspace_id))
    if row is None:
        raise DomainError(404, 'role_not_found', '角色不存在')
    if session.scalar(select(ServiceIdentity.id).where(ServiceIdentity.role_id == id)):
        raise DomainError(409, 'service_role_managed', '请在服务身份中修改权限')
    if row.revision != body.get('revision'):
        raise DomainError(409, 'revision_conflict', '角色已更新，请刷新后重试')
    validate_delegation(actor, row.permissions)
    if 'permissions' in body:
        body['permissions'] = validate_delegation(actor, body['permissions'])
    changed = session.execute(update(Role).where(Role.id == id, Role.workspace_id == actor.workspace_id,
        Role.revision == body['revision']).values(**{k: v for k, v in body.items() if k != 'revision'},
            revision=body['revision'] + 1), execution_options={'synchronize_session': False})
    if changed.rowcount != 1:
        raise DomainError(409, 'revision_conflict', '角色已更新，请刷新后重试')
    session.refresh(row)
    RecordStore(session, actor).audit('role.update', 'role', id)
    return role_dict(row)


def member_dict(row: Membership, user: User):
    return {'id': row.id, 'user_id': user.id, 'username': user.username, 'display_name': user.display_name,
            'role_ids': row.role_ids, 'department_id': row.department_id, 'disabled': row.disabled,
            'revision': row.revision, 'workspace_id': row.workspace_id}


@router.get('/members')
def members(actor: Actor = Depends(require_permission('m14.read')), session: Session = Depends(get_session)):
    service_users = select(ServiceIdentity.user_id)
    rows = session.execute(select(Membership, User).join(User, User.id == Membership.user_id)
                            .where(Membership.workspace_id == actor.workspace_id, User.id.not_in(service_users))).all()
    return {'items': [member_dict(m, u) for m, u in rows], 'total': len(rows)}


def validate_roles(session: Session, actor: Actor, ids: list):
    # 服务角色由专用身份接口维护，不能授予人类成员或从通用角色入口改写。
    if session.scalar(select(ServiceIdentity.id).where(ServiceIdentity.role_id.in_(ids))):
        raise DomainError(422, 'service_role_not_assignable', '请选择成员角色')
    roles = list(session.scalars(select(Role).where(Role.workspace_id == actor.workspace_id, Role.id.in_(ids))))
    if {row.id for row in roles} != set(ids):
        raise DomainError(422, 'invalid_role_scope', '所选角色不属于当前工作区')
    for role in roles:
        validate_delegation(actor, role.permissions)


@router.post('/members', status_code=201)
def create_member(body: dto.MemberCreate, actor: Actor = Depends(require_permission('m14.admin')),
                  session: Session = Depends(get_session)):
    body = body.model_dump(exclude_none=True)
    username = str(body.get('username', '')).casefold().strip()
    user = session.scalar(select(User).where(User.username == username)) if username else None
    role_ids = body.get('role_ids', [])
    validate_roles(session, actor, role_ids)
    if user is None:
        password = str(body.get('password', ''))
        if len(username) < 3 or len(password) < 12:
            raise DomainError(422, 'invalid_member', '请填写账号和至少12位初始密码', {'password': '至少12位'})
        user = User(organization_id=actor.organization_id, username=username,
                    display_name=str(body.get('display_name', username)), password_hash=PASSWORD_HASHER.hash(password))
        session.add(user)
        session.flush()
    if user.organization_id != actor.organization_id:
        raise DomainError(403, 'member_organization_denied', '无法添加该账号')
    if session.scalar(select(ServiceIdentity.id).where(ServiceIdentity.user_id == user.id)):
        raise DomainError(409, 'service_member_managed', '请在服务身份中管理该账号')
    if session.scalar(select(Membership.id).where(Membership.workspace_id == actor.workspace_id, Membership.user_id == user.id)):
        raise DomainError(409, 'member_exists', '该账号已是当前工作区成员')
    if body.get('department_id'):
        RecordStore(session, actor).get('department', body['department_id'])
    row = Membership(workspace_id=actor.workspace_id, user_id=user.id, role_ids=role_ids,
                     department_id=body.get('department_id'))
    session.add(row)
    session.flush()
    RecordStore(session, actor).audit('member.create', 'membership', row.id)
    return member_dict(row, user)


@router.patch('/members/{id}')
def patch_member(id: str, body: dto.MemberPatch, actor: Actor = Depends(require_permission('m14.admin')),
                 session: Session = Depends(get_session)):
    body = body.model_dump(exclude_unset=True)
    row = session.scalar(select(Membership).where(Membership.id == id, Membership.workspace_id == actor.workspace_id))
    if row is None:
        raise DomainError(404, 'member_not_found', '成员不存在')
    if session.scalar(select(ServiceIdentity.id).where(ServiceIdentity.user_id == row.user_id)):
        raise DomainError(409, 'service_member_managed', '请在服务身份中管理该账号')
    if row.revision != body.get('revision'):
        raise DomainError(409, 'revision_conflict', '成员已更新，请刷新后重试')
    if row.user_id == actor.user_id and body.get('disabled'):
        raise DomainError(409, 'cannot_disable_self', '不能停用当前登录账号')
    target_user = session.get(User, row.user_id)
    if target_user.is_admin and not actor.is_admin:
        raise DomainError(403, 'enterprise_admin_protected', '不能更改企业管理员的成员配置')
    validate_roles(session, actor, row.role_ids)
    if 'role_ids' in body:
        validate_roles(session, actor, body['role_ids'])
    if body.get('department_id'):
        RecordStore(session, actor).get('department', body['department_id'])
    changed = session.execute(update(Membership).where(Membership.id == id,
        Membership.workspace_id == actor.workspace_id, Membership.revision == body['revision']).values(
            **{k: v for k, v in body.items() if k != 'revision'}, revision=body['revision'] + 1),
        execution_options={'synchronize_session': False})
    if changed.rowcount != 1:
        raise DomainError(409, 'revision_conflict', '成员已更新，请刷新后重试')
    session.refresh(row)
    if row.disabled:
        session.execute(update(LoginSession).where(LoginSession.user_id == row.user_id,
            LoginSession.workspace_id == actor.workspace_id).values(revoked=True))
    RecordStore(session, actor).audit('member.update', 'membership', id)
    return member_dict(row, session.get(User, row.user_id))


@router.post('/session-revocations')
def revoke_session(body: dict, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    id = str(body.get('session_id', actor.session_id))
    row = session.get(LoginSession, id)
    if row is None:
        raise DomainError(404, 'session_not_found', '会话不存在')
    if row.user_id != actor.user_id:
        actor.require('m14.admin')
        user = session.get(User, row.user_id)
        if user.organization_id != actor.organization_id:
            raise DomainError(403, 'session_scope_denied', '无法撤销该会话')
        if not actor.is_admin:
            if row.workspace_id != actor.workspace_id or user.is_admin:
                raise DomainError(403, 'session_scope_denied', '只能撤销当前工作区内有权管理的会话')
            membership = session.scalar(select(Membership).where(Membership.user_id == user.id,
                Membership.workspace_id == actor.workspace_id))
            if membership is None:
                raise DomainError(403, 'session_scope_denied', '无法撤销该会话')
            validate_roles(session, actor, membership.role_ids)
    row.revoked = True
    RecordStore(session, actor).audit('session.revoke', 'session', id)
    return {'id': id, 'revoked': True}


@router.get('/resource-grants')
def grants(actor: Actor = Depends(require_permission('m14.read')), session: Session = Depends(get_session)):
    rows = list(session.scalars(select(ResourceGrant).where(ResourceGrant.workspace_id == actor.workspace_id)))
    return {'items': [{k: getattr(r, k) for k in ('id', 'resource_type', 'resource_id', 'user_id', 'actions')} for r in rows], 'total': len(rows)}


@router.post('/resource-grants', status_code=201)
def create_grant(body: dto.GrantCreate, request: Request, actor: Actor = Depends(require_permission('m14.admin')),
                 session: Session = Depends(get_session)):
    from .access import RESOURCE_MODULES
    from ...core.files import FileService
    body = body.model_dump()
    target = session.scalar(select(Membership).where(Membership.workspace_id == actor.workspace_id,
                            Membership.user_id == body.get('user_id'), Membership.disabled.is_(False)))
    if target is None:
        raise DomainError(422, 'grant_member_missing', '请先将用户加入当前工作区')
    kind = body['resource_type']
    module = RESOURCE_MODULES.get(kind)
    if not module:
        raise DomainError(422, 'grant_resource_unregistered', '该资源类型尚未登记对象授权')
    if kind == 'file' and set(body['actions']) != {'read'}:
        raise DomainError(422, 'file_grant_read_only', '文件授权当前只支持读取')
    record = FileService(session, actor, get_settings(request)).get(body['resource_id']) if kind == 'file' else RecordStore(session, actor).get(kind, body['resource_id'])
    if not actor.is_admin and record['owner_id'] != actor.user_id:
        raise DomainError(403, 'grant_owner_required', '只有资源所有者或企业管理员可以再授权')
    validate_delegation(actor, [f'{module}.{action}' for action in body['actions']])
    row = ResourceGrant(workspace_id=actor.workspace_id, resource_type=str(body['resource_type']),
                        resource_id=record['id'], user_id=target.user_id, actions=body.get('actions', ['read']))
    session.add(row)
    session.flush()
    RecordStore(session, actor).audit('grant.create', 'resource_grant', row.id)
    return {'id': row.id, 'resource_type': row.resource_type, 'resource_id': row.resource_id, 'user_id': row.user_id, 'actions': row.actions}


@router.delete('/resource-grants/{id}')
def delete_grant(id: str, actor: Actor = Depends(require_permission('m14.admin')),
                 session: Session = Depends(get_session)):
    row = session.scalar(select(ResourceGrant).where(ResourceGrant.id == id, ResourceGrant.workspace_id == actor.workspace_id))
    if row is None:
        raise DomainError(404, 'grant_not_found', '授权不存在')
    session.delete(row)
    RecordStore(session, actor).audit('grant.delete', 'resource_grant', id)
    return {'id': id, 'deleted': True}


from .credentials import router as credentials_router
from .access import router as access_router
from .service_accounts import router as service_accounts_router
from .client_certificate_router import router as client_certificate_router

router.include_router(credentials_router)
router.include_router(access_router)
router.include_router(service_accounts_router)
router.include_router(client_certificate_router)
