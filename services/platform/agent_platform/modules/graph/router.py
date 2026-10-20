"""图谱API：候选结果必须复核后写入，路径探索有规模与深度预算。"""

from hashlib import sha256
import json

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ...core.context import Actor, require_permission
from ...core.database import get_session
from ...core.errors import DomainError
from ...core.models import ResourceRecord
from ...core.records import RecordStore
from ...core.settings import get_settings
from ...core.tasks import TaskService
from .models import GraphEntity, GraphRelation
from .service import GraphService, entity_dict, validate_ontology

router = APIRouter(prefix='/api/v1', tags=['本体与图谱'])


@router.get('/ontologies')
def ontologies(q: str = '', actor: Actor = Depends(require_permission('m09.read')), session: Session = Depends(get_session)):
    return RecordStore(session, actor).list('ontology', q=q)


@router.post('/ontologies', status_code=201)
def create_ontology(body: dict, actor: Actor = Depends(require_permission('m09.write')), session: Session = Depends(get_session)):
    validate_ontology(body)
    if not str(body.get('name', '')).strip():
        raise DomainError(422, 'name_required', '请填写本体名称')
    return RecordStore(session, actor).create('ontology', {**body, 'status': 'draft'})


@router.get('/ontologies/{id}')
def ontology(id: str, actor: Actor = Depends(require_permission('m09.read')), session: Session = Depends(get_session)):
    return RecordStore(session, actor).get('ontology', id)


@router.patch('/ontologies/{id}')
def patch_ontology(id: str, body: dict, actor: Actor = Depends(require_permission('m09.write')), session: Session = Depends(get_session)):
    store = RecordStore(session, actor)
    old = store.get('ontology', id)
    data = {**old, **body}
    validate_ontology(data)
    store.create('ontology-revision', {'name': old['name'], 'ontology_id': id,
                 'number': old['revision'], 'snapshot': old})
    return store.update('ontology', id, data, body.get('revision', 0))


@router.delete('/ontologies/{id}')
def delete_ontology(id: str, revision: int, actor: Actor = Depends(require_permission('m09.delete')), session: Session = Depends(get_session)):
    linked = session.scalar(select(ResourceRecord.id).where(ResourceRecord.workspace_id == actor.workspace_id,
        ResourceRecord.kind == 'graph', ResourceRecord.deleted.is_(False), ResourceRecord.data['ontology_id'].as_string() == id))
    if linked:
        raise DomainError(409, 'ontology_in_use', '本体仍被图谱引用，请先处理图谱')
    return RecordStore(session, actor).delete('ontology', id, revision)


@router.get('/graphs')
def graphs(q: str = '', actor: Actor = Depends(require_permission('m09.read')), session: Session = Depends(get_session)):
    return RecordStore(session, actor).list('graph', q=q)


@router.post('/graphs', status_code=201)
def create_graph(body: dict, actor: Actor = Depends(require_permission('m09.write')), session: Session = Depends(get_session)):
    store = RecordStore(session, actor)
    ontology = store.get('ontology', str(body.get('ontology_id', '')))
    if not str(body.get('name', '')).strip():
        raise DomainError(422, 'name_required', '请填写图谱名称')
    return store.create('graph', {'name': body['name'], 'ontology_id': ontology['id'],
        'ontology_revision': ontology['revision'], 'ontology_snapshot': ontology, 'status': 'draft'},
        visibility=body.get('visibility', 'private'))


@router.get('/graphs/{id}')
def graph(id: str, actor: Actor = Depends(require_permission('m09.read')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    return GraphService(session, actor, settings).details(id)


@router.patch('/graphs/{id}')
def patch_graph(id: str, body: dict, actor: Actor = Depends(require_permission('m09.write')), session: Session = Depends(get_session)):
    changes = {key: body[key] for key in ('name', 'visibility', 'archived') if key in body}
    if 'name' in changes and (not isinstance(changes['name'], str) or not 0 < len(changes['name'].strip()) <= 200):
        raise DomainError(422, 'name_required', '请填写有效图谱名称')
    if 'archived' in changes and not isinstance(changes['archived'], bool):
        raise DomainError(422, 'archived_invalid', '归档状态格式不正确')
    return RecordStore(session, actor).update('graph', id, changes, body.get('revision', 0))


@router.delete('/graphs/{id}')
def delete_graph(id: str, revision: int, actor: Actor = Depends(require_permission('m09.delete')), session: Session = Depends(get_session)):
    store = RecordStore(session, actor)
    graph = store.get('graph', id)
    if graph.get('status') == 'published':
        raise DomainError(409, 'graph_published', '图谱已有发布版本，请先归档')
    return store.delete('graph', id, revision)


@router.post('/graphs/{id}/exports')
def export_graph(id: str, actor: Actor = Depends(require_permission('m09.read')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    service = GraphService(session, actor, settings)
    graph = service.details(id)
    if graph['truncated']:
        raise DomainError(422, 'graph_export_budget', '图谱超过单次导出预算，请分图导出')
    for item in [*graph['nodes'], *graph['edges']]:
        service.validate_refs(item['source_refs'])
    content = json.dumps({'schema_version': 1, **graph}, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return Response(content=content, media_type='application/json', headers={'Content-Disposition': 'attachment; filename="graph.json"'})


@router.post('/graphs/{id}/imports', status_code=201)
def import_graph(id: str, body: dict, actor: Actor = Depends(require_permission('m09.write')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    """结构化导入全量校验后写入；同图同名同类型同属性复用实体，禁止静默覆盖。"""
    service = GraphService(session, actor, settings)
    graph = service.graph(id)
    service.ensure_write(id)
    nodes, edges = body.get('nodes', []), body.get('edges', [])
    if not isinstance(nodes, list) or not 0 < len(nodes) <= 500 or not isinstance(edges, list) or len(edges) > 2000:
        raise DomainError(422, 'graph_import_budget', '单次导入需1到500个实体、最多2000条关系')
    known, types = {}, {}
    for number, node in enumerate(nodes, 1):
        if not isinstance(node, dict):
            raise DomainError(422, 'graph_import_row_invalid', f'第{number}个实体格式不正确')
        key = node.get('id', node.get('name'))
        if not isinstance(key, str) or not key or key in known:
            raise DomainError(422, 'graph_import_key_invalid', f'第{number}个实体标识为空或重复')
        service.validate_node(graph, node)
        known[key], types[key] = {**node, 'name': node['name'].strip()}, node['type']
    for number, edge in enumerate(edges, 1):
        if not isinstance(edge, dict) or edge.get('source') not in known or edge.get('target') not in known:
            raise DomainError(422, 'graph_import_edge_invalid', f'第{number}条关系引用了未知实体')
        service.validate_refs(edge.get('source_refs', []))
        if not any(rule['name'] == edge.get('type') and rule['source_type'] == types[edge['source']]
                   and rule['target_type'] == types[edge['target']] for rule in graph['ontology_snapshot'].get('relations', [])):
            raise DomainError(422, 'graph_import_relation_invalid', f'第{number}条关系类型或方向与本体不一致')
    result_nodes, result_edges, ids = [], [], {}
    for key, node in known.items():
        matches = list(session.scalars(select(GraphEntity).where(GraphEntity.workspace_id == actor.workspace_id,
            GraphEntity.graph_id == id, GraphEntity.name == node['name'], GraphEntity.type == node['type']).limit(2)))
        if len(matches) > 1 or (matches and matches[0].properties != node.get('properties', {})):
            raise DomainError(409, 'graph_import_merge_conflict', f'实体“{node["name"]}”已存在不同属性，请先人工合并')
        value = entity_dict(matches[0]) if matches else service.add_node(id, node)
        ids[key] = value['id']
        result_nodes.append(value['id'])
    for edge in edges:
        value = service.add_relation(id, {**edge, 'source': ids[edge['source']], 'target': ids[edge['target']]})
        result_edges.append(value['id'])
    digest = sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    service.store.audit('graph.import', 'graph', id, {'input_hash': digest, 'entities': len(nodes), 'relations': len(edges)})
    return {'graph_id': id, 'entity_ids': result_nodes, 'relation_ids': result_edges, 'input_hash': digest}


@router.post('/graphs/{id}/quality-checks')
def check_graph_quality(id: str, actor: Actor = Depends(require_permission('m09.read')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    graph = GraphService(session, actor, settings).details(id)
    connected = {edge[key] for edge in graph['edges'] for key in ('source', 'target')}
    groups = {}
    for node in graph['nodes']:
        groups.setdefault((node['name'], node['type']), []).append(node['id'])
    issues = [{'kind': 'isolated_entity', 'entity_ids': [node['id']]} for node in graph['nodes'] if node['id'] not in connected]
    issues.extend({'kind': 'duplicate_name', 'entity_ids': ids} for ids in groups.values() if len(ids) > 1)
    return {'graph_id': id, 'entity_count': len(graph['nodes']), 'relation_count': len(graph['edges']),
            'issues': issues, 'total': len(issues), 'truncated': graph['truncated']}


@router.post('/graphs/{id}/queries')
def query_graph(id: str, body: dict, actor: Actor = Depends(require_permission('m09.read')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    return GraphService(session, actor, settings).explore(id, body)


@router.post('/graphs/{id}/entities', status_code=201)
def create_entity(id: str, body: dict, actor: Actor = Depends(require_permission('m09.write')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    return GraphService(session, actor, settings).add_node(id, body)


@router.post('/graphs/{id}/relations', status_code=201)
def create_relation(id: str, body: dict, actor: Actor = Depends(require_permission('m09.write')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    return GraphService(session, actor, settings).add_relation(id, body)


@router.patch('/graphs/{id}/entities/{entity_id}')
def patch_entity(id: str, entity_id: str, body: dict, actor: Actor = Depends(require_permission('m09.write')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    service = GraphService(session, actor, settings)
    service.ensure_write(id)
    row = service.node(id, entity_id)
    if body.get('revision') != row.revision:
        raise DomainError(409, 'revision_conflict', '实体已更新，请刷新后重试')
    candidate = {**entity_dict(row), **body}
    service.validate_node(service.graph(id), candidate)
    if candidate['type'] != row.type and session.scalar(select(GraphRelation.id).where(GraphRelation.graph_id == id,
        (GraphRelation.source == entity_id) | (GraphRelation.target == entity_id))):
        raise DomainError(409, 'entity_type_in_use', '实体已有关联关系，请先处理关系后再更改类型')
    changed = session.execute(update(GraphEntity).where(GraphEntity.id == entity_id, GraphEntity.graph_id == id,
        GraphEntity.workspace_id == actor.workspace_id, GraphEntity.revision == body['revision'])
        .values(name=candidate['name'], type=candidate['type'], properties=candidate['properties'],
                source_refs=candidate['source_refs'], revision=body['revision'] + 1)).rowcount
    if changed != 1:
        raise DomainError(409, 'revision_conflict', '实体已更新，请刷新后重试')
    session.refresh(row)
    service.touch(id)
    service.store.audit('graph.entity.update', 'graph', id, {'entity_id': entity_id})
    return entity_dict(row)


@router.delete('/graphs/{id}/entities/{entity_id}')
def delete_entity(id: str, entity_id: str, revision: int, actor: Actor = Depends(require_permission('m09.delete')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    service = GraphService(session, actor, settings)
    service.ensure_write(id)
    row = service.node(id, entity_id)
    if row.revision != revision:
        raise DomainError(409, 'revision_conflict', '实体已更新，请刷新')
    related = session.scalar(select(GraphRelation.id).where(GraphRelation.graph_id == id,
                              (GraphRelation.source == entity_id) | (GraphRelation.target == entity_id)))
    if related:
        raise DomainError(409, 'entity_has_relations', '实体仍有关联关系，请先处理关系')
    changed = session.execute(delete(GraphEntity).where(GraphEntity.id == entity_id, GraphEntity.graph_id == id,
        GraphEntity.workspace_id == actor.workspace_id, GraphEntity.revision == revision)).rowcount
    if changed != 1:
        raise DomainError(409, 'revision_conflict', '实体已更新，请刷新后重试')
    service.touch(id)
    service.store.audit('graph.entity.delete', 'graph', id, {'entity_id': entity_id})
    return {'id': entity_id, 'deleted': True}


@router.delete('/graphs/{id}/relations/{relation_id}')
def delete_relation(id: str, relation_id: str, revision: int, actor: Actor = Depends(require_permission('m09.delete')), session: Session = Depends(get_session)):
    RecordStore(session, actor).assert_action('graph', id, 'write')
    row = session.scalar(select(GraphRelation).where(GraphRelation.id == relation_id, GraphRelation.graph_id == id,
                          GraphRelation.workspace_id == actor.workspace_id))
    if row is None:
        raise DomainError(404, 'relation_not_found', '关系不存在')
    if row.revision != revision:
        raise DomainError(409, 'revision_conflict', '关系已更新，请刷新')
    changed = session.execute(delete(GraphRelation).where(GraphRelation.id == relation_id, GraphRelation.graph_id == id,
        GraphRelation.workspace_id == actor.workspace_id, GraphRelation.revision == revision)).rowcount
    if changed != 1:
        raise DomainError(409, 'revision_conflict', '关系已更新，请刷新后重试')
    graph = RecordStore(session, actor).get('graph', id)
    RecordStore(session, actor).update('graph', id, {'status': 'draft'}, graph['revision'])
    RecordStore(session, actor).audit('graph.relation.delete', 'graph', id, {'relation_id': relation_id})
    return {'id': relation_id, 'deleted': True}


@router.post('/graphs/{id}/publications')
def publish_graph(id: str, body: dict, actor: Actor = Depends(require_permission('m09.publish')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    service = GraphService(session, actor, settings)
    service.ensure_write(id)
    graph = service.details(id)
    if graph['truncated']:
        raise DomainError(422, 'graph_publication_budget', '当前图谱超过单次发布预算，请拆分图谱')
    snapshot = service.store.create('graph-publication', {'name': graph['name'], 'graph_id': id, 'snapshot': graph}, visibility=graph['visibility'])
    return service.store.update('graph', id, {'status': 'published', 'publication_id': snapshot['id']}, body.get('revision', 0))


@router.get('/extraction-jobs')
def extraction_jobs(actor: Actor = Depends(require_permission('m09.read')), session: Session = Depends(get_session)):
    return RecordStore(session, actor).list('extraction-job')


@router.post('/extraction-jobs', status_code=201)
def extract(body: dict, actor: Actor = Depends(require_permission('m09.write')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    return GraphService(session, actor, settings).extract(body)


@router.get('/extraction-jobs/{id}')
def extraction(id: str, actor: Actor = Depends(require_permission('m09.read')), session: Session = Depends(get_session)):
    return RecordStore(session, actor).get('extraction-job', id)


@router.post('/extraction-jobs/{id}/reviews')
def review_extraction(id: str, body: dict, actor: Actor = Depends(require_permission('m09.write')), session: Session = Depends(get_session), settings=Depends(get_settings)):
    service = GraphService(session, actor, settings)
    row = service.store.get('extraction-job', id)
    if body.get('decision') not in {'approve', 'reject'}:
        raise DomainError(422, 'decision_required', '请选择通过或拒绝')
    if row['status'] != 'pending_review':
        return row
    if body.get('decision') == 'reject':
        TaskService(session, actor).transition(row['task_id'], 'cancelled')
        return service.store.update('extraction-job', id, {'status': 'rejected', 'reviewed_by': actor.user_id}, body.get('revision', 0))
    graph_id = str(body.get('graph_id', ''))
    service.ensure_write(graph_id)
    graph = service.graph(graph_id)
    if graph['ontology_id'] != row['ontology_id']:
        raise DomainError(422, 'ontology_mismatch', '目标图谱与抽取本体不一致')
    nodes = []
    for candidate in row['candidates']:
        existing_id = body.get('merge_targets', {}).get(candidate['name'])
        if existing_id:
            entity = service.node(graph_id, existing_id)
            if entity.type != candidate['type']:
                raise DomainError(422, 'merge_type_mismatch', '不能合并不同类型的实体')
            refs = [*entity.source_refs, *candidate.get('source_refs', [])]
            service.validate_node(graph, {**entity_dict(entity), 'source_refs': refs})
            entity.source_refs = refs
            entity.revision += 1
            service.touch(graph_id)
            nodes.append(entity_dict(entity))
        else:
            nodes.append(service.add_node(graph_id, candidate))
    ids_by_name = {node['name']: node['id'] for node in nodes}
    relations = []
    for relation in row.get('relations', []):
        relations.append(service.add_relation(graph_id, {**relation,
            'source': ids_by_name.get(relation.get('source'), relation.get('source')),
            'target': ids_by_name.get(relation.get('target'), relation.get('target'))}))
    service.store.update('extraction-job', id, {'status': 'approved', 'reviewed_by': actor.user_id,
        'graph_id': graph_id, 'entity_ids': [n['id'] for n in nodes],
        'relation_ids': [r['id'] for r in relations]}, body.get('revision', 0))
    TaskService(session, actor).transition(row['task_id'], 'succeeded', outputs={'graph_id': graph_id, 'entity_ids': [n['id'] for n in nodes]})
    return service.store.get('extraction-job', id)
