"""本体约束、有限图探索与抽取复核；不把关键词匹配冒充模型抽取。"""

import csv
from io import StringIO
import json
import math

from sqlalchemy import or_, select

from ...core.errors import DomainError
from ...core.files import FileService
from ...core.records import RecordStore
from ...core.tasks import TaskService
from .models import GraphEntity, GraphRelation


def entity_dict(row):
    return {k: getattr(row, k) for k in ('id', 'name', 'type', 'properties', 'source_refs', 'revision')}


def relation_dict(row):
    return {k: getattr(row, k) for k in ('id', 'source', 'target', 'type', 'properties', 'source_refs', 'revision')}


def validate_ontology(data: dict):
    concepts = data.get('concepts', [])
    if not isinstance(concepts, list) or not concepts or len(concepts) > 100 or any(not isinstance(c, dict) for c in concepts):
        raise DomainError(422, 'concepts_required', '请添加至少一个实体类型')
    names = [str(c.get('name', '')).strip() for c in concepts]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise DomainError(422, 'concept_names_invalid', '实体类型名称不能为空或重复')
    for c in concepts:
        properties = c.get('properties', {})
        if not isinstance(properties, dict) or any(t not in {'string', 'number', 'boolean'} for t in properties.values()):
            raise DomainError(422, 'property_type_invalid', '属性类型请选择文本、数字或布尔值')
    relations = data.get('relations', [])
    if not isinstance(relations, list) or any(not isinstance(r, dict) for r in relations):
        raise DomainError(422, 'relation_type_invalid', '关系类型格式不正确')
    for r in relations:
        if not r.get('name') or r.get('source_type') not in names or r.get('target_type') not in names:
            raise DomainError(422, 'relation_type_invalid', '请为关系选择已有的起点和终点类型')


class GraphService:
    def __init__(self, session, actor, settings):
        self.session, self.actor, self.settings = session, actor, settings
        self.store = RecordStore(session, actor)

    def graph(self, id):
        return self.store.get('graph', id)

    def ensure_write(self, id):
        self.store.assert_action('graph', id, 'write')

    def touch(self, id):
        graph = self.graph(id)
        self.store.update('graph', id, {'status': 'draft'}, graph['revision'])

    def node(self, graph_id, id):
        self.graph(graph_id)
        row = self.session.scalar(select(GraphEntity).where(GraphEntity.id == id,
            GraphEntity.graph_id == graph_id, GraphEntity.workspace_id == self.actor.workspace_id))
        if row is None:
            raise DomainError(404, 'entity_not_found', '实体不存在或不属于当前图谱')
        return row

    def validate_node(self, graph, data):
        ontology = graph['ontology_snapshot']
        concept = next((c for c in ontology['concepts'] if c['name'] == data.get('type')), None)
        if concept is None or not str(data.get('name', '')).strip() or len(str(data.get('name', ''))) > 240:
            raise DomainError(422, 'entity_invalid', '请填写实体名称并选择已有类型')
        props = data.get('properties', {})
        allowed = concept.get('properties', {})
        if not isinstance(props, dict) or set(props) - set(allowed):
            raise DomainError(422, 'entity_properties_invalid', '实体包含本体未声明的属性')
        for key, value in props.items():
            expected = allowed[key]
            valid = (expected == 'string' and isinstance(value, str)) or (
                expected == 'number' and isinstance(value, (int, float)) and not isinstance(value, bool)) or (
                expected == 'boolean' and isinstance(value, bool))
            if not valid:
                raise DomainError(422, 'entity_property_type_invalid', '属性值与本体类型不一致', {key: '请检查属性类型'})
            if isinstance(value, float) and not math.isfinite(value):
                raise DomainError(422, 'entity_property_not_finite', '数字属性必须是有限值')
        self.validate_refs(data.get('source_refs', []))
        if len(json.dumps(props, ensure_ascii=False)) > 65536:
            raise DomainError(422, 'entity_budget_exceeded', '属性或来源数量超过单实体预算')

    def validate_refs(self, refs):
        if not isinstance(refs, list) or len(refs) > 50 or any(not isinstance(ref, dict) for ref in refs):
            raise DomainError(422, 'source_refs_invalid', '来源格式不正确或超过50项')
        for ref in refs:
            if ref.get('file_id'):
                FileService(self.session, self.actor, self.settings).get(ref['file_id'])

    def add_node(self, graph_id, data):
        graph = self.graph(graph_id)
        self.ensure_write(graph_id)
        self.validate_node(graph, data)
        row = GraphEntity(workspace_id=self.actor.workspace_id, graph_id=graph_id,
            name=str(data['name']).strip(), type=data['type'], properties=data.get('properties', {}),
            source_refs=data.get('source_refs', []))
        self.session.add(row)
        self.session.flush()
        self.touch(graph_id)
        self.store.audit('graph.entity.create', 'graph', graph_id, {'entity_id': row.id})
        return entity_dict(row)

    def add_relation(self, graph_id, data):
        graph = self.graph(graph_id)
        self.ensure_write(graph_id)
        source, target = self.node(graph_id, data.get('source')), self.node(graph_id, data.get('target'))
        valid = any(r['name'] == data.get('type') and r['source_type'] == source.type and r['target_type'] == target.type
                    for r in graph['ontology_snapshot'].get('relations', []))
        if not valid:
            raise DomainError(422, 'relation_not_declared', '关系类型或实体方向与本体不一致')
        self.validate_refs(data.get('source_refs', []))
        if not isinstance(data.get('properties', {}), dict) or len(json.dumps(data.get('properties', {}), ensure_ascii=False)) > 65536:
            raise DomainError(422, 'relation_properties_invalid', '关系属性格式不正确或超过预算')
        existing = self.session.scalar(select(GraphRelation).where(GraphRelation.graph_id == graph_id,
            GraphRelation.workspace_id == self.actor.workspace_id, GraphRelation.source == source.id,
            GraphRelation.target == target.id, GraphRelation.type == data['type']))
        if existing:
            return relation_dict(existing)
        row = GraphRelation(workspace_id=self.actor.workspace_id, graph_id=graph_id,
            source=source.id, target=target.id, type=data['type'], properties=data.get('properties', {}),
            source_refs=data.get('source_refs', []))
        self.session.add(row)
        self.session.flush()
        self.touch(graph_id)
        self.store.audit('graph.relation.create', 'graph', graph_id, {'relation_id': row.id})
        return relation_dict(row)

    def details(self, graph_id, limit=500):
        graph = self.graph(graph_id)
        nodes = list(self.session.scalars(select(GraphEntity).where(GraphEntity.graph_id == graph_id,
            GraphEntity.workspace_id == self.actor.workspace_id).order_by(GraphEntity.name).limit(limit + 1)))
        ids = [n.id for n in nodes[:limit]]
        edges = self.session.scalars(select(GraphRelation).where(GraphRelation.graph_id == graph_id,
            GraphRelation.workspace_id == self.actor.workspace_id, GraphRelation.source.in_(ids),
            GraphRelation.target.in_(ids)).limit(2000))
        return {**graph, 'nodes': [entity_dict(r) for r in nodes[:limit]],
                'edges': [relation_dict(r) for r in edges], 'truncated': len(nodes) > limit}

    def explore(self, graph_id, body):
        graph = self.graph(graph_id)
        start, q = body.get('entity_id'), str(body.get('q', '')).casefold()
        try:
            depth = int(body.get('depth', 1))
            limit = min(max(int(body.get('limit', 500)), 1), 500)
        except (ValueError, TypeError) as exc:
            raise DomainError(422, 'graph_query_invalid', '层数和数量须为整数') from exc
        if not 1 <= depth <= 8:
            raise DomainError(422, 'depth_out_of_range', '请选择1到8层关系范围')
        if not start and not q:
            return self.details(graph_id, limit=limit)
        scope = [GraphEntity.workspace_id == self.actor.workspace_id, GraphEntity.graph_id == graph_id]
        seeds = [self.node(graph_id, start)] if start else list(self.session.scalars(select(GraphEntity)
            .where(*scope, GraphEntity.name.ilike('%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%', escape='\\'))
            .order_by(GraphEntity.id).limit(limit + 1)))
        nodes = {node.id: node for node in seeds[:limit]}
        truncated = len(seeds) > limit
        frontier, edges, parents = set(nodes), {}, {id: None for id in nodes}
        target = body.get('target_id')
        if target:
            self.node(graph_id, target)
        # 按当前前沿查询，不能先截取图谱前500项再假称找不到更远的真实节点。
        for _ in range(depth):
            if not frontier or (target and target in nodes):
                break
            candidates = list(self.session.scalars(select(GraphRelation).where(
                GraphRelation.workspace_id == self.actor.workspace_id, GraphRelation.graph_id == graph_id,
                or_(GraphRelation.source.in_(frontier), GraphRelation.target.in_(frontier)))
                .order_by(GraphRelation.id).limit(2001)))
            truncated = truncated or len(candidates) > 2000
            next_frontier = set()
            for edge in candidates[:2000]:
                for origin, neighbor in ((edge.source, edge.target), (edge.target, edge.source)):
                    if origin in frontier and neighbor not in nodes:
                        if len(nodes) >= limit:
                            truncated = True
                            continue
                        nodes[neighbor] = self.node(graph_id, neighbor)
                        parents[neighbor] = origin
                        next_frontier.add(neighbor)
                if edge.source in nodes and edge.target in nodes:
                    if len(edges) < 2000:
                        edges[edge.id] = edge
                    else:
                        truncated = True
            frontier = next_frontier
        path = []
        if target and target in nodes:
            current = target
            while current is not None:
                path.append(current)
                current = parents[current]
            path.reverse()
        return {**graph, 'nodes': [entity_dict(node) for node in nodes.values()],
                'edges': [relation_dict(edge) for edge in edges.values()], 'truncated': truncated,
                'path': path, 'path_found': bool(path) if target else None}

    def extract(self, data):
        ontology = self.store.get('ontology', str(data.get('ontology_id', '')))
        mode = data.get('mode', 'structured')
        source = []
        if data.get('file_id'):
            files = FileService(self.session, self.actor, self.settings)
            file = files.get(data['file_id'])
            text = files.read_text(file['id'])
            source = [{'file_id': file['id'], 'revision': file['revision'], 'sha256': file['sha256']}]
            if mode == 'structured':
                try:
                    records = json.loads(text) if file['name'].lower().endswith('.json') else list(csv.DictReader(StringIO(text)))
                except (ValueError, csv.Error) as exc:
                    raise DomainError(422, 'extraction_file_invalid', '文件内容不是有效JSON或CSV') from exc
            else:
                data = {**data, 'text': text}
        else:
            records = data.get('records', [])
        candidates = []
        if mode == 'structured':
            if not isinstance(records, list) or not 0 < len(records) <= 1000:
                raise DomainError(422, 'extraction_records_invalid', '请提供1到1000行结构化记录')
            concept = next((c for c in ontology['concepts'] if c['name'] == data.get('entity_type')), None)
            if concept is None:
                raise DomainError(422, 'extraction_type_required', '请选择抽取的实体类型')
            for index, record in enumerate(records):
                name = record.get(data.get('name_field', 'name'))
                if not name:
                    continue
                properties = {key: record[field] for key, field in data.get('field_mapping', {}).items() if field in record}
                candidate = {'name': str(name), 'type': concept['name'], 'properties': properties,
                             'source_refs': [{**ref, 'row': index + 1} for ref in source]}
                # 使用和人工编辑相同的本体校验，错误数据不能先进入正式图谱。
                self.validate_node({'ontology_snapshot': ontology}, candidate)
                candidates.append(candidate)
        elif mode == 'terms':
            text = str(data.get('text', ''))
            terms = data.get('terms', [])
            if len(text) > 200000 or not terms or len(terms) > 500:
                raise DomainError(422, 'extraction_terms_invalid', '请提供文本和限定的实体词表')
            for term in terms:
                start = text.find(str(term.get('name', '')))
                if start >= 0 and term.get('name'):
                    candidate = {'name': term['name'], 'type': term['type'], 'properties': term.get('properties', {}),
                                 'source_refs': [{**ref, 'start': start, 'end': start + len(term['name'])} for ref in source]}
                    self.validate_node({'ontology_snapshot': ontology}, candidate)
                    candidates.append(candidate)
        else:
            raise DomainError(422, 'extraction_mode_not_configured', '当前未配置所选抽取服务')
        tasks = TaskService(self.session, self.actor)
        task = tasks.create('graph-extraction', str(data.get('name', '实体抽取')), {'ontology_id': ontology['id'], 'mode': mode})
        tasks.transition(task['id'], 'running')
        row = self.store.create('extraction-job', {'name': str(data.get('name', '实体抽取')),
            'ontology_id': ontology['id'], 'ontology_revision': ontology['revision'],
            'mode': mode, 'candidates': candidates, 'relations': data.get('relations', []),
            'source_refs': source, 'task_id': task['id'], 'status': 'pending_review'}, visibility='private')
        tasks.transition(task['id'], 'waiting_review', outputs={'job_id': row['id'], 'candidate_count': len(candidates)})
        return row
