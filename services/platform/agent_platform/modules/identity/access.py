"""岗位、权限预览和已注册原生入口；预览复用实际仓储授权，而非前端模拟。"""

from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit
import re

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Literal

from ...core.context import Actor, get_actor, require_permission, resolve_actor
from ...core.database import get_session
from ...core.errors import DomainError
from ...core.files import FileService
from ...core.models import FileEntry, Membership, ResourceGrant, ResourceRecord, User, Workspace
from ...core.records import RecordStore
from ...core.settings import get_settings
from ...registry import MODULES
from .models import ServiceIdentity
from .schemas import IdentityInput, RevisionInput
from .security import ACTIONS, PERMISSION_UNIVERSE

router = APIRouter()

# 对应后端真实注册的资源类型；未登记类型不能凭一个通用“已授权”掩盖领域差异。
RESOURCE_MODULES = {
    'application': 'm13', 'application-publication': 'm13', 'industry-package': 'm13',
    'skill-binding': 'm12', 'tool_connection': 'm12', 'department': 'm14', 'position': 'm14',
    'integration_instance': 'm15', 'model_profile': 'm15', 'external_identity': 'm14',
    'external_workspace': 'm14', 'agent_app_binding': 'm03', 'automation': 'm04',
    'knowledge_space': 'm08', 'knowledge_document': 'm08', 'knowledge_citation': 'm08',
    'business_metric': 'm05', 'data_relation': 'm05', 'query_template': 'm06',
    'artifact': 'm17', 'file': 'm17', 'report_prompt': 'm07',
    'ontology': 'm09', 'ontology-revision': 'm09', 'graph': 'm09',
    'graph-publication': 'm09', 'extraction-job': 'm09',
}


def object_allowed(session, actor: Actor, resource_type: str, id: str, action: str, request: Request) -> bool:
    """只确认当前数据权限，不执行业务写入或读取文件内容。"""
    if resource_type == 'file':
        FileService(session, actor, get_settings(request)).get(id)
        row = session.get(FileEntry, id)
        if action == 'read' or actor.is_admin or row.owner_id == actor.user_id:
            return True
        raise DomainError(403, 'file_owner_required', '只有文件所有者或管理员可更改文件')
    store = RecordStore(session, actor)
    store.get(resource_type, id)
    if action != 'read':
        store.assert_action(resource_type, id, action)
    return True


class PositionCreate(IdentityInput):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=2000)
    department_id: str
    member_ids: list[str] = Field(default_factory=list, max_length=1000)
    enabled: bool = True


class PositionPatch(RevisionInput):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    department_id: str | None = None
    member_ids: list[str] | None = Field(default=None, max_length=1000)
    enabled: bool | None = None


def validate_position(session, actor, data):
    if not data.get('department_id'):
        raise DomainError(422, 'position_department_required', '请选择岗位所属部门')
    RecordStore(session, actor).get('department', data['department_id'])
    members = set(data.get('member_ids', []))
    actual = set(session.scalars(select(Membership.id).where(Membership.workspace_id == actor.workspace_id,
        Membership.id.in_(members), Membership.disabled.is_(False))))
    if actual != members:
        raise DomainError(422, 'position_members_invalid', '岗位成员必须来自当前工作区的在用成员')


@router.get('/positions')
def positions(actor: Actor = Depends(require_permission('m14.read')), session: Session = Depends(get_session)):
    return RecordStore(session, actor).list('position', limit=100)


@router.post('/positions', status_code=201)
def create_position(body: PositionCreate, actor: Actor = Depends(require_permission('m14.admin')), session: Session = Depends(get_session)):
    data = body.model_dump()
    validate_position(session, actor, data)
    return RecordStore(session, actor).create('position', data)


@router.patch('/positions/{id}')
def patch_position(id: str, body: PositionPatch, actor: Actor = Depends(require_permission('m14.admin')), session: Session = Depends(get_session)):
    store = RecordStore(session, actor)
    changes = body.model_dump(exclude_unset=True, exclude={'revision'})
    validate_position(session, actor, {**store.get('position', id), **changes})
    return store.update('position', id, changes, body.revision)


@router.get('/permissions/navigation')
def navigation(request: Request, actor: Actor = Depends(get_actor)):
    enabled = {module.key for module in request.app.state.modules}
    items = []
    for module in MODULES:
        if module.key not in enabled:
            continue
        features = []
        for feature in module.feature_modules:
            permissions = [f'{feature.lower()}.{action}' for action in ACTIONS if actor.can(f'{feature.lower()}.{action}')]
            if permissions:
                features.append({'id': feature, 'permissions': permissions})
        if features:
            items.append({'key': module.key, 'name': module.name, 'feature_modules': features})
    return {'items': items, 'total': len(items), 'permissions': [p for p in PERMISSION_UNIVERSE if actor.can(p)]}


class PermissionPreview(IdentityInput):
    principal_id: str
    workspace_id: str | None = None
    resource_id: str | None = None
    resource_type: str | None = None
    permission: str | None = Field(default=None, pattern=r'^m\d{2}\.(read|write|delete|publish|admin|audit)$')
    action: Literal['read', 'write', 'delete'] = 'read'


@router.post('/permission-previews')
def preview(body: PermissionPreview, request: Request, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    if body.principal_id != actor.user_id:
        actor.require('m14.admin')
    target = session.get(User, body.principal_id)
    workspace_id = body.workspace_id or actor.workspace_id
    workspace = session.get(Workspace, workspace_id)
    if target is None or workspace is None or target.organization_id != actor.organization_id or workspace.organization_id != actor.organization_id:
        raise DomainError(404, 'preview_subject_missing', '目标身份或工作区不存在')
    if not actor.is_admin and workspace_id != actor.workspace_id:
        raise DomainError(403, 'preview_workspace_denied', '只能预览当前工作区权限')
    result = {'principal_id': target.id, 'workspace_id': workspace_id, 'resource_id': body.resource_id, 'action': body.action, 'allowed': False}
    try:
        effective = resolve_actor(session, SimpleNamespace(user_id=target.id, workspace_id=workspace_id, id='permission-preview'))
        service = session.scalar(select(ServiceIdentity).where(ServiceIdentity.user_id == target.id))
        if service:
            if not service.enabled or service.workspace_id != workspace_id:
                raise DomainError(403, 'service_identity_disabled', '服务身份已停用')
            scoped = replace(effective, permissions=frozenset(service.permissions))
            effective = replace(effective, permissions=frozenset(p for p in PERMISSION_UNIVERSE if effective.can(p) and scoped.can(p)))
        resource_type = body.resource_type
        if body.resource_id and not resource_type:
            record = session.scalar(select(ResourceRecord).where(ResourceRecord.id == body.resource_id, ResourceRecord.workspace_id == workspace_id, ResourceRecord.deleted.is_(False)))
            resource_type = record.kind if record else None
        module = RESOURCE_MODULES.get(resource_type or '')
        permission = f'{module}.{body.action}' if module else body.permission
        if body.resource_id and (not module or (body.permission and body.permission != permission)):
            raise DomainError(422, 'resource_permission_unregistered', '该资源尚未登记权限预览规则')
        if permission:
            effective.require(permission)
        if body.resource_id:
            if not resource_type:
                raise DomainError(404, 'resource_not_found', '资源不存在')
            object_allowed(session, effective, resource_type, body.resource_id, body.action, request)
        return {**result, 'allowed': True, 'permission': permission, 'roles': list(effective.roles), 'permissions': sorted(effective.permissions)}
    except DomainError as exc:
        return {**result, 'reason': exc.code}


class AppLaunch(IdentityInput):
    application: Literal['openwebui', 'dify', 'qknow', 'analytics']
    return_path: str = Field(default='/', min_length=1, max_length=500)
    integration_id: str | None = None
    target: str | None = Field(default=None, max_length=40)
    native_id: str | None = Field(default=None, max_length=300)


def native_target(application, path):
    """仅把已核对的原生相对路径转成适配器支持的入口，拒绝开放重定向。"""
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not path.startswith('/') or '\\' in path or '%' in path:
        raise DomainError(422, 'launch_path_invalid', '请选择已登记的应用入口')
    if application == 'openwebui':
        known = {'/': 'chat', '/workspace/models': 'models', '/workspace/knowledge': 'knowledge', '/workspace/tools': 'tools', '/admin': 'admin'}
        if path in known:
            return known[path], None
        if re.fullmatch(r'/c/[\w-]+', path):
            return 'chat', path[3:]
    else:
        known = {'/': 'apps', '/apps': 'apps', '/datasets': 'knowledge', '/agents': 'agent_v2', '/plugins': 'plugins'}
        if path in known:
            return known[path], None
        patterns = [(r'/app/([\w-]+)/workflow', 'workflow'), (r'/app/([\w-]+)/configuration', 'agent'), (r'/datasets/([\w-]+)/documents', 'knowledge'), (r'/agents/([\w-]+)(?:/configure)?', 'agent_v2')]
        for pattern, target in patterns:
            matched = re.fullmatch(pattern, path)
            if matched:
                return target, matched[1]
    raise DomainError(422, 'launch_path_unregistered', '该应用入口未登记')


@router.post('/app-launches')
def launch_app(body: AppLaunch, request: Request, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    from ..integrations.service import INTEGRATION, IntegrationService
    service = IntegrationService(session, actor, request)
    if body.application in {'qknow', 'analytics'}:
        # 内部入口来自现有前端已注册页面，仍检查相应模块权限，不生成虚构SSO令牌。
        entries = {'/overview': 'm01.read', '/applications': 'm13.read', '/files': 'm17.read', '/relations': 'm09.read', '/analysis': 'm06.read', '/reports': 'm07.read', '/sources': 'm05.read', '/quality': 'm06.read', '/jobs': 'm06.read'}
        path = body.return_path if body.return_path != '/' else ('/analysis' if body.application == 'analytics' else '/overview')
        if path not in entries:
            raise DomainError(422, 'launch_path_unregistered', '该平台入口未登记')
        actor.require(entries[path])
        result = {'url': path, 'requires_native_login': False, 'mode': 'platform_route'}
    else:
        target, native_id = (body.target, body.native_id) if body.target else native_target(body.application, body.return_path)
        permissions = {'chat': 'm02.read', 'model_chat': 'm02.read', 'models': 'm15.read', 'tools': 'm12.read', 'admin': 'm14.admin', 'knowledge': 'm08.read', 'apps': 'm13.read', 'agent': 'm03.read', 'agent_v2': 'm03.read', 'workflow': 'm04.read', 'plugins': 'm12.admin'}
        if target not in permissions:
            raise DomainError(422, 'launch_target_invalid', '请选择已登记的应用入口')
        actor.require(permissions[target])
        if body.integration_id:
            integration = service.integration(body.integration_id)
        else:
            candidates = [item for item in service.list_all(INTEGRATION) if item['engine'] == body.application and item.get('enabled', True)]
            if not candidates:
                raise DomainError(503, 'integration_not_configured', '请先配置该应用的服务连接')
            if len(candidates) != 1:
                raise DomainError(409, 'integration_selection_required', '请选择要打开的服务连接', {'integration_id': '请选择连接'})
            integration = candidates[0]
        if integration['engine'] != body.application:
            raise DomainError(422, 'launch_engine_mismatch', '所选连接与应用类型不一致')
        result = {**service.launch(integration['id'], target, native_id), 'mode': 'native_deep_link'}
    launch = service.store.create('app-launch', {'name': body.application, 'application': body.application, 'url': result['url'], 'user_id': actor.user_id, 'mode': result['mode']}, visibility='private')
    return {**result, 'id': launch['id'], 'application': body.application}
