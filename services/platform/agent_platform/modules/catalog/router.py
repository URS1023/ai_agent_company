"""对有共同生命周期的配置复用仓储，发布和行业安装执行各自业务校验。"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.context import Actor, get_actor, require_permission
from ...core.database import get_session
from ...core.errors import DomainError
from ...core.models import ResourceRecord, ResourceReference
from ...core.records import RecordStore

router = APIRouter(prefix='/api/v1', tags=['应用与能力目录'])


class ConfigResource(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=4000)


def validate_resource(kind: str, data: dict, store: RecordStore, id: str | None = None):
    if 'name' in data and not str(data['name']).strip():
        raise DomainError(422, 'name_required', '请填写名称', {'name': '请输入名称'})
    if kind == 'application':
        entry = str(data.get('entry', ''))
        if entry and (not entry.startswith('/') or entry.startswith('//')):
            raise DomainError(422, 'invalid_application_entry', '请选择平台内页面作为入口')
        if data.get('engine', 'platform') not in {'platform', 'dify', 'openwebui'}:
            raise DomainError(422, 'invalid_engine', '请选择有效的应用来源')
    if kind == 'department' and data.get('parent_id'):
        parent = store.get('department', data['parent_id'])
        seen = {id} if id else set()
        while parent:
            if parent['id'] in seen:
                raise DomainError(422, 'department_cycle', '部门层级不能形成循环')
            seen.add(parent['id'])
            parent = store.get('department', parent['parent_id']) if parent.get('parent_id') else None
    if kind == 'skill-binding':
        schema = data.get('input_schema')
        if schema is not None:
            from jsonschema import Draft202012Validator, SchemaError
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise DomainError(422, 'invalid_input_schema', '输入Schema格式不正确') from exc


def register_config(path: str, kind: str, module: str):
    """只装配配置类资源的共同生命周期，领域计算和发布均另有接口。"""
    read = require_permission(module + '.read')
    write = require_permission(module + '.write')
    remove = require_permission(module + '.delete')

    def listing(q: str = '', limit: int = 50, offset: int = 0, include_archived: bool = False,
                actor: Actor = Depends(read), session: Session = Depends(get_session)):
        return RecordStore(session, actor).list(kind, q=q, limit=limit, offset=offset, include_archived=include_archived)

    def create(body: ConfigResource, actor: Actor = Depends(write), session: Session = Depends(get_session)):
        data = body.model_dump()
        store = RecordStore(session, actor)
        validate_resource(kind, data, store)
        if kind in {'application', 'skill-binding'}:
            data['status'] = 'draft'
        return store.create(kind, data, visibility=data.get('visibility', 'workspace'))

    def get(id: str, actor: Actor = Depends(read), session: Session = Depends(get_session)):
        return RecordStore(session, actor).get(kind, id)

    def patch(id: str, body: dict, actor: Actor = Depends(write), session: Session = Depends(get_session)):
        store = RecordStore(session, actor)
        old = store.get(kind, id)
        data = {**old, **body}
        validate_resource(kind, data, store, id)
        if kind in {'application', 'skill-binding'} and old.get('status') == 'published':
            data['status'] = 'draft'
        return store.update(kind, id, data, body.get('revision', 0))

    def delete(id: str, revision: int, actor: Actor = Depends(remove), session: Session = Depends(get_session)):
        store = RecordStore(session, actor)
        row = store.get(kind, id)
        references = list(session.scalars(select(ResourceReference).where(
            ResourceReference.workspace_id == actor.workspace_id,
            ResourceReference.target_type == kind, ResourceReference.target_id == id)))
        if references or row.get('status') == 'published':
            raise DomainError(409, 'resource_in_use', '资源已有发布或引用，请先停用或归档')
        return store.delete(kind, id, revision)

    router.add_api_route(path, listing, methods=['GET'], name=kind + '_list')
    router.add_api_route(path, create, methods=['POST'], status_code=201, name=kind + '_create')
    router.add_api_route(path + '/{id}', get, methods=['GET'], name=kind + '_get')
    router.add_api_route(path + '/{id}', patch, methods=['PATCH'], name=kind + '_patch')
    router.add_api_route(path + '/{id}', delete, methods=['DELETE'], name=kind + '_delete')


for path, kind, module in (
    ('/applications', 'application', 'm13'), ('/skill-bindings', 'skill-binding', 'm12'),
    ('/departments', 'department', 'm14'),
):
    register_config(path, kind, module)


@router.post('/applications/{id}/publications')
def publish_application(id: str, body: dict, actor: Actor = Depends(require_permission('m13.publish')),
                         session: Session = Depends(get_session)):
    store = RecordStore(session, actor)
    row = store.get('application', id)
    if row.get('engine', 'platform') != 'platform' and not all(row.get(k) for k in ('integration_id', 'resource_id', 'version')):
        raise DomainError(422, 'native_binding_required', '请先绑定来源应用、固定版本和连接')
    if row.get('engine', 'platform') == 'platform' and not row.get('entry'):
        raise DomainError(422, 'entry_required', '请选择应用入口')
    publication = store.create('application-publication', {'name': row['name'], 'application_id': id,
                                  'snapshot': row, 'version': row.get('version') or str(row['revision'])})
    return store.update('application', id, {'status': 'published', 'publication_id': publication['id']}, body.get('revision', 0))


BUILTIN_CAPABILITIES = (
    ('query.readonly', '受控只读查询', 'data', 'm06.read'),
    ('inspection.assess', '结构化质检判定', 'manufacturing', 'm11.write'),
    ('maintenance.statistics', '同工况维护分析', 'manufacturing', 'm10.write'),
    ('report.render', '结构化报表生成', 'reports', 'm07.write'),
    ('document.generate', '办公文档生成', 'files', 'm17.write'),
    ('knowledge.retrieve', 'Dify企业知识检索', 'integrations', 'm08.read'),
    ('graph.search', '本体约束图谱探索', 'graph', 'm09.read'),
)


@router.get('/capabilities')
def capabilities(request: Request, actor: Actor = Depends(require_permission('m12.read'))):
    enabled = {m.key for m in request.app.state.modules}
    rows = [{'id': id, 'name': name, 'module': module, 'permission': permission,
             'status': 'enabled' if module in enabled else 'disabled'}
            for id, name, module, permission in BUILTIN_CAPABILITIES if actor.can(permission)]
    return {'items': rows, 'total': len(rows)}


@router.get('/capabilities/{id}')
def capability(id: str, request: Request, actor: Actor = Depends(require_permission('m12.read'))):
    rows = capabilities(request, actor)['items']
    row = next((r for r in rows if r['id'] == id), None)
    if row is None:
        raise DomainError(404, 'capability_not_found', '能力不存在或未获授权')
    return row


@router.post('/extension-validations')
def validate_extension(body: dict, request: Request, actor: Actor = Depends(require_permission('m12.admin'))):
    required = {'name', 'version', 'capabilities'}
    if not required.issubset(body) or not isinstance(body['capabilities'], list):
        raise DomainError(422, 'invalid_extension_manifest', '请提供名称、版本和能力清单')
    available = {r[0] for r in BUILTIN_CAPABILITIES}
    unknown = set(body['capabilities']) - available
    return {'valid': not unknown, 'unknown_capabilities': sorted(unknown), 'name': body['name'],
            'version': body['version'], 'executable_code_installed': False}


INDUSTRY_PACKAGES = (
    {'id': 'food-quality', 'name': '食品设备与包装质量', 'version': '1.0', 'industry': '食品',
     'applications': [{'name': '食品设备检查', 'entry': '/device-check'},
                      {'name': '包装批次质检', 'entry': '/product-check'}]},
    {'id': 'chemical-maintenance', 'name': '化工泵维护', 'version': '1.0', 'industry': '化工',
     'applications': [{'name': '离心泵维护分析', 'entry': '/maintenance'}]},
    {'id': 'textile-quality', 'name': '纺织设备与质量', 'version': '1.0', 'industry': '纺织',
     'applications': [{'name': '织机设备检查', 'entry': '/device-check'},
                      {'name': '产品批次质检', 'entry': '/product-check'}]},
)


@router.get('/industry-packages')
def industry_packages(actor: Actor = Depends(require_permission('m13.read'))):
    return {'items': list(INDUSTRY_PACKAGES), 'total': len(INDUSTRY_PACKAGES)}


@router.get('/industry-packages/{id}')
def industry_package(id: str, actor: Actor = Depends(require_permission('m13.read'))):
    row = next((p for p in INDUSTRY_PACKAGES if p['id'] == id), None)
    if row is None:
        raise DomainError(404, 'package_not_found', '行业包不存在')
    return row


@router.post('/industry-package-installations', status_code=201)
def install_package(body: dict, actor: Actor = Depends(require_permission('m13.admin')),
                      session: Session = Depends(get_session)):
    package = industry_package(str(body.get('package_id', '')), actor)
    if body.get('version') != package['version']:
        raise DomainError(409, 'package_version_mismatch', '请选择当前可用的行业包版本')
    store = RecordStore(session, actor)
    existing = next((r for r in store.list('industry-installation')['items'] if
                     r.get('package_id') == package['id'] and r.get('version') == package['version']), None)
    if existing:
        return existing
    apps = [store.create('application', {**a, 'engine': 'platform', 'industry': package['industry'],
            'version': package['version'], 'status': 'draft', 'package_id': package['id']}) for a in package['applications']]
    return store.create('industry-installation', {'name': package['name'], 'package_id': package['id'],
                        'version': package['version'], 'application_ids': [a['id'] for a in apps], 'status': 'installed'})
