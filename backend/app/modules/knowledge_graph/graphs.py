"""Knowledge graph editing, exploration, statistics and immutable publication."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.common.audit import audit
from app.common.authorization import require_permission, require_resource
from app.common.deps import AuthContext
from app.common.owned_records import owned_record, visible_records
from app.errors import ConflictError, NotFoundError, ValidationError
from app.modules.knowledge_graph import ontology as ontology_service
from app.modules.knowledge_graph.models import (
    GraphEntity,
    GraphRelation,
    GraphRevision,
    KnowledgeGraph,
    OntologyConcept,
    OntologyRelationType,
)
from app.modules.knowledge_graph.schemas import (
    AttributeDefinition, GraphEntityInput, GraphInput, GraphRelationInput, PublishInput, property_value_key,
)


def _iso(value: datetime | None) -> str | None:
    """时间转 ISO 字符串；None 原样返回 None。"""
    return value.isoformat() if value else None


def _version(record, expected: int | None, *, creating: bool = False) -> None:
    """统一乐观锁校验：新建不得带 version，修改必须带且一致。异常：ValidationError / ConflictError。"""
    if creating:
        if expected is not None:
            raise ValidationError("新建记录不能提交 version")
    elif expected is None:
        raise ValidationError("修改记录必须提交 version")
    elif record.version != expected:
        raise ConflictError("记录已被其他操作修改，请刷新后重试", current_version=record.version)


def _commit(ctx: AuthContext, message: str) -> None:
    """提交事务并把唯一约束冲突翻译成 ConflictError。message 为面向用户的冲突文案。"""
    try:
        ctx.db.commit()
    except IntegrityError as exc:
        ctx.db.rollback()
        raise ConflictError(message) from exc
    except StaleDataError as exc:
        ctx.db.rollback()
        raise ConflictError("记录已被其他操作修改，请刷新后重试") from exc


def get_graph(ctx: AuthContext, graph_id: str, permission: str = "graphs.view") -> KnowledgeGraph:
    """取图谱并做归属/权限校验。异常：NotFoundError（不存在或越权）、PermissionError。"""
    row = owned_record(ctx, KnowledgeGraph, graph_id, permission)
    if row.status == "deleted":
        raise NotFoundError("知识图谱不存在")
    return row


def _active_entities(ctx: AuthContext, graph_id: str) -> list[GraphEntity]:
    """取图谱中未删除的实体，供视图与统计复用。"""
    return ctx.db.scalars(select(GraphEntity).where(
        GraphEntity.workspace_id == ctx.workspace_id, GraphEntity.graph_id == graph_id,
        GraphEntity.status != "deleted",
    ).order_by(GraphEntity.created_at)).all()


def _active_relations(ctx: AuthContext, graph_id: str) -> list[GraphRelation]:
    """取图谱中未删除的关系，供视图与统计复用。"""
    return ctx.db.scalars(select(GraphRelation).where(
        GraphRelation.workspace_id == ctx.workspace_id, GraphRelation.graph_id == graph_id,
        GraphRelation.status != "deleted",
    ).order_by(GraphRelation.created_at)).all()


def graph_view(ctx: AuthContext, row: KnowledgeGraph, *, detail: bool = False) -> dict:
    """图谱视图；detail=True 时才附带本体与绑定修订等明细字段。"""
    entity_count = ctx.db.scalar(select(func.count(GraphEntity.id)).where(
        GraphEntity.workspace_id == ctx.workspace_id, GraphEntity.graph_id == row.id,
        GraphEntity.status != "deleted",
    )) or 0
    relation_count = ctx.db.scalar(select(func.count(GraphRelation.id)).where(
        GraphRelation.workspace_id == ctx.workspace_id, GraphRelation.graph_id == row.id,
        GraphRelation.status != "deleted",
    )) or 0
    result = {
        "id": row.id, "name": row.name, "description": row.description,
        "ontologyId": row.ontology_id, "ontologyRevision": row.ontology_revision,
        "status": row.status, "publishedRevision": row.published_revision,
        "entityCount": int(entity_count), "relationCount": int(relation_count),
        "version": row.version, "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
        "source": "智慧云脑 Cloud Brain",
    }
    if detail:
        result["statistics"] = graph_statistics(ctx, row.id, graph=row)
    return result


def entity_view(row: GraphEntity, concept: OntologyConcept | None = None) -> dict:
    """图谱实体的对外序列化；传 concept 时会带上概念的属性定义，便于前端渲染表单。"""
    return {
        "id": row.id, "graphId": row.graph_id, "conceptId": row.concept_id,
        "conceptName": concept.name if concept else None,
        "businessKey": row.business_key, "name": row.name, "properties": row.properties,
        "sourceCandidateId": row.source_candidate_id, "provenance": row.provenance,
        "status": row.status, "version": row.version,
        "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
    }


def relation_view(row: GraphRelation, relation_type: OntologyRelationType | None = None) -> dict:
    """图谱关系的对外序列化；传 relation_type 时会带上关系类型名与基数。"""
    return {
        "id": row.id, "graphId": row.graph_id, "relationTypeId": row.relation_type_id,
        "relationName": relation_type.name if relation_type else None,
        "sourceEntityId": row.source_entity_id, "targetEntityId": row.target_entity_id,
        "properties": row.properties, "sourceCandidateId": row.source_candidate_id,
        "provenance": row.provenance, "status": row.status, "version": row.version,
        "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
    }


def list_graphs(ctx: AuthContext, keyword: str = "") -> list[dict]:
    """列出当前账号可见的图谱（keyword 匹配名称/描述）。可见性由 visible_records 决定。"""
    statement = visible_records(ctx, KnowledgeGraph, "graphs.view").where(KnowledgeGraph.status != "deleted")
    clean = keyword.strip()
    if clean:
        statement = statement.where((KnowledgeGraph.name.contains(clean)) | (KnowledgeGraph.description.contains(clean)))
    rows = ctx.db.scalars(statement.order_by(KnowledgeGraph.updated_at.desc()).limit(500)).all()
    return [graph_view(ctx, row) for row in rows]


def save_graph(ctx: AuthContext, payload: GraphInput, graph_id: str | None = None) -> dict:
    """新建或更新图谱元信息（名称/描述/绑定的本体）。
    校验要点：本体必须可见且已发布（图谱的实体/关系校验依赖冻结的元模型快照）。
    异常：ValidationError、NotFoundError、ConflictError（重名或并发）。
    """
    permission = "graphs.edit" if graph_id else "graphs.create"
    require_permission(ctx, permission)
    ontology, revision = ontology_service.published_snapshot(ctx, payload.ontology_id)
    if graph_id:
        row = get_graph(ctx, graph_id, permission)
        _version(row, payload.version)
        if row.ontology_id != ontology.id:
            used = bool(_active_entities(ctx, row.id) or _active_relations(ctx, row.id))
            if used:
                raise ConflictError("已有实体或关系的图谱不能更换本体")
            row.ontology_id = ontology.id
            row.ontology_revision = revision.revision
    else:
        _version(None, payload.version, creating=True)
        row = KnowledgeGraph(
            workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
            ontology_id=ontology.id, ontology_revision=revision.revision,
        )
        ctx.db.add(row)
    row.name = payload.name.strip()
    row.description = payload.description.strip()
    if row.status in {"archived", "deleted"}:
        row.status = "draft"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="graphs.update" if graph_id else "graphs.create", resource_type="knowledge_graph",
          resource_id=row.id, metadata={"ontology_id": ontology.id, "ontology_revision": revision.revision})
    _commit(ctx, "图谱名称已存在")
    return graph_view(ctx, row, detail=True)


def _snapshot_definitions(ctx: AuthContext, graph: KnowledgeGraph) -> tuple[dict[str, dict], dict[str, dict]]:
    """读取该图谱绑定的本体正式修订里的 (概念定义表, 关系类型定义表)，用于校验实体/关系。"""
    _, revision = ontology_service.published_snapshot(ctx, graph.ontology_id, graph.ontology_revision)
    snapshot = revision.snapshot
    concepts = {item["id"]: item for item in snapshot.get("concepts", [])}
    relations = {item["id"]: item for item in snapshot.get("relationTypes", [])}
    return concepts, relations


def _validate_properties(definitions: list[dict], values: dict, label: str) -> dict[str, AttributeDefinition]:
    """按固定定义校验值并返回已解析定义；历史定义歧义、类型或枚举错误均拒绝。

    历史字符串枚举只在新模型对象中解释，不写回固定快照，也不转换业务属性值。
    """
    known = {}
    for definition in definitions:
        try:
            item = AttributeDefinition.model_validate(definition)
        except ValueError as exc:
            raise ValidationError(f"{label}属性定义无效：{exc}") from exc
        if item.name in known:
            raise ValidationError(f"{label}属性字段名不能重复：{item.name}")
        known[item.name] = item
    unknown = set(values) - set(known)
    if unknown:
        raise ValidationError(f"{label}包含未定义属性：" + "、".join(sorted(unknown)))
    for name, definition in known.items():
        value = values.get(name)
        if definition.required and (value is None or value == ""):
            raise ValidationError(f"{label}缺少必填属性：{definition.label}")
        if value is None:
            continue
        try:
            key = property_value_key(definition.type, value)
        except (ValueError, TypeError) as exc:
            raise ValidationError(f"{label}属性 {definition.label} 类型应为合法的 {definition.type}") from exc
        if definition.enum_values and not any(
            key == property_value_key(definition.type, item) for item in definition.enum_values
        ):
            raise ValidationError(f"{label}属性 {definition.label} 不在允许枚举中")
    return known


def _lock_graph_write(ctx: AuthContext, graph_id: str, permission: str) -> KnowledgeGraph:
    """先锁已授权图谱父行，再访问子记录；手工和物化写入使用同一锁顺序。

    锁由调用方事务持有，commit=False 只 flush、不提前释放。当前读取不能省略：
    请求可能早已建立 MySQL RR 快照，父锁之后的唯一候选也必须使用锁定读取。
    """
    graph = ctx.db.scalar(visible_records(ctx, KnowledgeGraph, permission).where(
        KnowledgeGraph.id == graph_id,
    ).with_for_update().execution_options(populate_existing=True))
    if graph is None or graph.status == "deleted":
        raise NotFoundError("知识图谱不存在")
    require_resource(ctx, permission, graph)
    return graph


def _enforce_unique_properties(ctx: AuthContext, graph: KnowledgeGraph, model, type_column,
                               type_id: str, definitions: dict[str, AttributeDefinition],
                               values: dict, current_id: str | None = None) -> None:
    """在已持有父锁时核对同图谱、同类型的有效记录；空值不占用，空字符串是实际值。"""
    unique = {name: item for name, item in definitions.items()
              if item.unique and values.get(name) is not None}
    if not unique:
        return
    statement = select(model).where(
        model.workspace_id == ctx.workspace_id, model.graph_id == graph.id,
        type_column == type_id, model.status != "deleted",
    )
    if current_id is not None:
        statement = statement.where(model.id != current_id)
    # 按主键取得锁定当前读，避开旧事务快照；不按子记录创建人过滤图谱内的完整约束集。
    rows = ctx.db.scalars(statement.order_by(model.id).with_for_update()
        .execution_options(populate_existing=True)).all()
    for name, definition in unique.items():
        key = property_value_key(definition.type, values[name])
        for row in rows:
            previous = (row.properties or {}).get(name)
            if previous is None:
                continue
            try:
                previous_key = property_value_key(definition.type, previous)
            except (ValueError, TypeError):
                # 旧版可能存过非法日期；它不属于合法值域，不能阻塞多条坏记录逐条修复。
                # 本次新值已通过严格校验，因此忽略此旧值不会放行重复的合法唯一值。
                continue
            if key == previous_key:
                raise ConflictError(f"同一图谱类型内的唯一属性 {definition.label} 已存在")


def _concept_row(ctx: AuthContext, graph: KnowledgeGraph, concept_id: str) -> tuple[OntologyConcept, dict]:
    """按 concept_id 取本体概念与其定义字典（来自图谱绑定的修订快照）。异常：ValidationError。"""
    concepts, _ = _snapshot_definitions(ctx, graph)
    definition = concepts.get(concept_id)
    row = ctx.db.get(OntologyConcept, concept_id)
    if not definition or not row or row.workspace_id != ctx.workspace_id or row.ontology_id != graph.ontology_id:
        raise ValidationError("概念不属于图谱固定使用的本体修订")
    return row, definition


def _entity(ctx: AuthContext, graph: KnowledgeGraph, entity_id: str) -> GraphEntity:
    """取图谱实体并校验归属与未删除状态。异常：NotFoundError。"""
    row = ctx.db.get(GraphEntity, entity_id)
    if not row or row.workspace_id != ctx.workspace_id or row.graph_id != graph.id or row.status == "deleted":
        raise NotFoundError("图谱实体不存在")
    return row


def save_entity(ctx: AuthContext, graph_id: str, payload: GraphEntityInput,
                entity_id: str | None = None, *, provenance: dict | None = None,
                source_candidate_id: str | None = None, commit: bool = True) -> dict:
    """新建或更新图谱实体（业务键 + 属性值）。
    校验要点：concept_id 必须是本图谱绑定本体里的概念；business_key 在概念下唯一；
    属性值按概念 schema 校验（见 _validate_properties）；entities 的删除是软删（status=deleted）。
    异常：ValidationError、NotFoundError、ConflictError。
    """
    permission = "graphs.edit" if entity_id else "graphs.create"
    graph = _lock_graph_write(ctx, graph_id, permission)
    concept, definition = _concept_row(ctx, graph, payload.concept_id)
    attributes = _validate_properties(definition.get("attributes", []), payload.properties, "实体")
    if entity_id:
        row = _entity(ctx, graph, entity_id)
        _version(row, payload.version)
        if row.concept_id != concept.id:
            raise ValidationError("不能修改已有实体的概念类型")
    else:
        _version(None, payload.version, creating=True)
    _enforce_unique_properties(ctx, graph, GraphEntity, GraphEntity.concept_id,
        concept.id, attributes, payload.properties, entity_id)
    if entity_id is None:
        row = GraphEntity(
            workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, graph_id=graph.id,
            concept_id=concept.id, source_candidate_id=source_candidate_id,
        )
        ctx.db.add(row)
    row.business_key = payload.business_key.strip()
    row.name = payload.name.strip()
    row.properties = payload.properties
    row.provenance = provenance or row.provenance or {"source": "manual", "actorUserId": ctx.user_id}
    row.status = "active"
    if commit:
        audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
              action="graphs.entity.update" if entity_id else "graphs.entity.create",
              resource_type="graph_entity", resource_id=row.id, metadata={"graph_id": graph.id})
        _commit(ctx, "同一概念的实体业务键已存在")
    else:
        ctx.db.flush()
    return entity_view(row, concept)


def _relation_type(ctx: AuthContext, graph: KnowledgeGraph, relation_type_id: str) -> tuple[OntologyRelationType, dict]:
    """按 relation_type_id 取本体关系类型与其定义字典。异常：ValidationError。"""
    _, definitions = _snapshot_definitions(ctx, graph)
    definition = definitions.get(relation_type_id)
    row = ctx.db.get(OntologyRelationType, relation_type_id)
    if not definition or not row or row.workspace_id != ctx.workspace_id or row.ontology_id != graph.ontology_id:
        raise ValidationError("关系类型不属于图谱固定使用的本体修订")
    return row, definition


def _enforce_cardinality(ctx: AuthContext, graph: KnowledgeGraph, relation_type_id: str, cardinality: str,
                         source_id: str, target_id: str, current_id: str | None = None) -> None:
    """按关系类型声明的基数约束校验本条关系是否可写入（一对一/一对多/多对多的目标端上限）。
    异常：ValidationError（超出基数限制）。
    """
    rows = ctx.db.scalars(select(GraphRelation).where(
        GraphRelation.workspace_id == ctx.workspace_id, GraphRelation.graph_id == graph.id,
        GraphRelation.relation_type_id == relation_type_id, GraphRelation.status != "deleted",
    )).all()
    rows = [row for row in rows if row.id != current_id]
    if cardinality == "one_to_one" and any(
        row.source_entity_id == source_id or row.target_entity_id == target_id for row in rows
    ):
        raise ConflictError("一对一关系的起点或终点已被占用")
    if cardinality == "one_to_many" and any(row.target_entity_id == target_id for row in rows):
        raise ConflictError("一对多关系的目标实体已关联其他起点")
    if cardinality == "many_to_one" and any(row.source_entity_id == source_id for row in rows):
        raise ConflictError("多对一关系的起点实体已关联其他终点")


def save_relation(ctx: AuthContext, graph_id: str, payload: GraphRelationInput,
                  relation_id: str | None = None, *, provenance: dict | None = None,
                  source_candidate_id: str | None = None, commit: bool = True) -> dict:
    """新建或更新图谱关系（source→target + 关系类型）。
    校验要点：两端实体必须存在且属于本图谱；关系类型必须来自绑定本体；写入前先过基数校验；
    同一 (类型, 源, 目标) 三元组不允许重复。异常：ValidationError、NotFoundError、ConflictError。
    """
    permission = "graphs.edit" if relation_id else "graphs.create"
    graph = _lock_graph_write(ctx, graph_id, permission)
    relation_type, definition = _relation_type(ctx, graph, payload.relation_type_id)
    source = _entity(ctx, graph, payload.source_entity_id)
    target = _entity(ctx, graph, payload.target_entity_id)
    expected = (definition.get("sourceConceptId"), definition.get("targetConceptId"))
    actual = (source.concept_id, target.concept_id)
    if actual != expected and not (not definition.get("directed", True) and actual == tuple(reversed(expected))):
        raise ValidationError("实体概念与关系类型的起点、终点约束不一致")
    attributes = _validate_properties(definition.get("attributes", []), payload.properties, "关系")
    if relation_id:
        row = ctx.db.get(GraphRelation, relation_id)
        if not row or row.workspace_id != ctx.workspace_id or row.graph_id != graph.id or row.status == "deleted":
            raise NotFoundError("图谱关系不存在")
        _version(row, payload.version)
    else:
        _version(None, payload.version, creating=True)
    _enforce_unique_properties(ctx, graph, GraphRelation, GraphRelation.relation_type_id,
        relation_type.id, attributes, payload.properties, relation_id)
    if relation_id is None:
        row = GraphRelation(
            workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, graph_id=graph.id,
            source_candidate_id=source_candidate_id,
        )
        ctx.db.add(row)
    _enforce_cardinality(
        ctx, graph, relation_type.id, definition.get("cardinality", "many_to_many"),
        source.id, target.id, row.id if relation_id else None,
    )
    row.relation_type_id = relation_type.id
    row.source_entity_id = source.id
    row.target_entity_id = target.id
    row.properties = payload.properties
    row.provenance = provenance or row.provenance or {"source": "manual", "actorUserId": ctx.user_id}
    row.status = "active"
    if commit:
        audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
              action="graphs.relation.update" if relation_id else "graphs.relation.create",
              resource_type="graph_relation", resource_id=row.id, metadata={"graph_id": graph.id})
        _commit(ctx, "同类型、同起点和终点的关系已存在")
    else:
        ctx.db.flush()
    return relation_view(row, relation_type)


def list_entities(ctx: AuthContext, graph_id: str, *, keyword: str = "", concept_id: str = "",
                  limit: int = 100, offset: int = 0) -> list[dict]:
    """分页/筛选查询图谱实体（keyword 匹配名称与业务键，concept_id 限定概念）。异常：NotFoundError。"""
    graph = get_graph(ctx, graph_id, "graphs.view")
    statement = select(GraphEntity).where(
        GraphEntity.workspace_id == ctx.workspace_id, GraphEntity.graph_id == graph.id,
        GraphEntity.status != "deleted",
    )
    if keyword.strip():
        statement = statement.where((GraphEntity.name.contains(keyword.strip())) | (GraphEntity.business_key.contains(keyword.strip())))
    if concept_id:
        statement = statement.where(GraphEntity.concept_id == concept_id)
    rows = ctx.db.scalars(statement.order_by(GraphEntity.updated_at.desc()).offset(offset).limit(limit)).all()
    concepts = {row.id: row for row in ctx.db.scalars(select(OntologyConcept).where(
        OntologyConcept.id.in_({item.concept_id for item in rows})
    )).all()} if rows else {}
    return [entity_view(row, concepts.get(row.concept_id)) for row in rows]


def list_relations(ctx: AuthContext, graph_id: str, *, limit: int = 100, offset: int = 0) -> list[dict]:
    """按 limit/offset 列出图谱关系，默认 100 条。异常：NotFoundError。"""
    graph = get_graph(ctx, graph_id, "graphs.view")
    rows = ctx.db.scalars(select(GraphRelation).where(
        GraphRelation.workspace_id == ctx.workspace_id, GraphRelation.graph_id == graph.id,
        GraphRelation.status != "deleted",
    ).order_by(GraphRelation.updated_at.desc()).offset(offset).limit(limit)).all()
    types = {row.id: row for row in ctx.db.scalars(select(OntologyRelationType).where(
        OntologyRelationType.id.in_({item.relation_type_id for item in rows})
    )).all()} if rows else {}
    return [relation_view(row, types.get(row.relation_type_id)) for row in rows]


def remove_entity(ctx: AuthContext, graph_id: str, entity_id: str, version: int) -> None:
    """软删除实体，并连带软删与其相连的关系（避免悬挂边）。异常：NotFoundError、ConflictError。"""
    graph = get_graph(ctx, graph_id, "graphs.delete")
    row = _entity(ctx, graph, entity_id)
    _version(row, version)
    linked = ctx.db.scalar(select(func.count(GraphRelation.id)).where(
        GraphRelation.graph_id == graph.id, GraphRelation.status != "deleted",
        (GraphRelation.source_entity_id == row.id) | (GraphRelation.target_entity_id == row.id),
    )) or 0
    if linked:
        raise ConflictError("实体仍有关联关系，请先移除关系")
    row.status = "deleted"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="graphs.entity.delete", resource_type="graph_entity", resource_id=row.id)
    _commit(ctx, "实体删除失败")


def remove_relation(ctx: AuthContext, graph_id: str, relation_id: str, version: int) -> None:
    """软删除一条关系。异常：NotFoundError、ConflictError。"""
    graph = get_graph(ctx, graph_id, "graphs.delete")
    row = ctx.db.get(GraphRelation, relation_id)
    if not row or row.workspace_id != ctx.workspace_id or row.graph_id != graph.id or row.status == "deleted":
        raise NotFoundError("图谱关系不存在")
    _version(row, version)
    row.status = "deleted"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="graphs.relation.delete", resource_type="graph_relation", resource_id=row.id)
    _commit(ctx, "关系删除失败")


def graph_statistics(ctx: AuthContext, graph_id: str, *, graph: KnowledgeGraph | None = None) -> dict:
    """统计实体数、关系数、按概念分布等，供图谱概览与健康度展示。
    参数 graph 可复用已取到的图谱行，避免在批量场景重复查询。
    """
    graph = graph or get_graph(ctx, graph_id, "graphs.view")
    entities = _active_entities(ctx, graph.id)
    relations = _active_relations(ctx, graph.id)
    by_concept: dict[str, int] = {}
    by_relation: dict[str, int] = {}
    for row in entities:
        by_concept[row.concept_id] = by_concept.get(row.concept_id, 0) + 1
    for row in relations:
        by_relation[row.relation_type_id] = by_relation.get(row.relation_type_id, 0) + 1
    return {
        "entityCount": len(entities), "relationCount": len(relations),
        "relationTypeCount": len(by_relation), "conceptCount": len(by_concept),
        "entitiesByConcept": by_concept, "relationsByType": by_relation,
        "isolatedEntityCount": len({row.id for row in entities} - {
            entity_id for relation in relations for entity_id in (relation.source_entity_id, relation.target_entity_id)
        }),
    }


def explore(ctx: AuthContext, graph_id: str, *, query: str = "", entity_id: str = "",
            depth: int = 1, limit: int = 200) -> dict:
    """图谱探索：以关键字检索实体，并返回其关联实体与关系（用于可视化定位）。
    参数：query 为关键字；entity_id 指定时以该实体为中心展开。
    异常：NotFoundError（图谱不存在）。
    """
    graph = get_graph(ctx, graph_id, "graphs.view")
    # 同一创建时间以主键稳定排序；探索顺序先保留起点，再按距离逐层展开。
    all_entities = sorted(_active_entities(ctx, graph.id), key=lambda row: (row.created_at, row.id))
    all_relations = _active_relations(ctx, graph.id)
    entity_by_id = {row.id: row for row in all_entities}
    rank = {row.id: index for index, row in enumerate(all_entities)}
    if entity_id:
        if entity_id not in entity_by_id:
            raise NotFoundError("探索起点实体不存在")
        seeds = [entity_id]
    elif query.strip():
        term = query.strip().lower()
        seeds = [row.id for row in all_entities if term in (row.name + " " + row.business_key + " " + json.dumps(row.properties, ensure_ascii=False)).lower()]
    else:
        seeds = [row.id for row in all_entities]
    ordered_selected = seeds[:limit]
    selected = set(ordered_selected)
    truncated = len(seeds) > limit
    if (entity_id or query.strip()) and not truncated:
        neighbors = {item: set() for item in entity_by_id}
        for relation in all_relations:
            source, target = relation.source_entity_id, relation.target_entity_id
            if source in neighbors and target in neighbors:
                neighbors[source].add(target)
                neighbors[target].add(source)
        frontier = selected.copy()
        for _ in range(depth):
            connected = {target for item in frontier for target in neighbors[item]} - selected
            if not connected:
                break
            remaining = limit - len(ordered_selected)
            ordered_selected.extend(sorted(connected, key=rank.__getitem__)[:remaining])
            # 恰好满额还需检查允许深度内的下一层；有合格节点被省略才算截断。
            if len(connected) > remaining:
                truncated = True
                selected = set(ordered_selected)
                break
            selected |= connected
            frontier = connected
    relations = [row for row in all_relations if row.source_entity_id in selected and row.target_entity_id in selected]
    concepts = {row.id: row for row in ctx.db.scalars(select(OntologyConcept).where(
        OntologyConcept.id.in_({entity_by_id[item].concept_id for item in selected})
    )).all()} if selected else {}
    relation_types = {row.id: row for row in ctx.db.scalars(select(OntologyRelationType).where(
        OntologyRelationType.id.in_({item.relation_type_id for item in relations})
    )).all()} if relations else {}
    nodes = []
    for item in ordered_selected:
        row = entity_by_id[item]
        concept = concepts.get(row.concept_id)
        nodes.append({**entity_view(row, concept), "label": row.name, "color": concept.color if concept else "#6483ad"})
    edges = [{**relation_view(row, relation_types.get(row.relation_type_id)),
              "source": row.source_entity_id, "target": row.target_entity_id,
              "label": relation_types[row.relation_type_id].name if row.relation_type_id in relation_types else "关系"}
             for row in relations]
    return {"graph": graph_view(ctx, graph), "nodes": nodes, "edges": edges,
            "truncated": truncated, "depth": depth, "query": query}


def _snapshot(ctx: AuthContext, graph: KnowledgeGraph) -> dict:
    """生成图谱发布快照（实体 + 关系 + 本体修订号）。"""
    entities = _active_entities(ctx, graph.id)
    relations = _active_relations(ctx, graph.id)
    return {
        "graph": {"id": graph.id, "name": graph.name, "description": graph.description,
                  "ontologyId": graph.ontology_id, "ontologyRevision": graph.ontology_revision},
        "entities": [entity_view(row) for row in entities],
        "relations": [relation_view(row) for row in relations],
        "statistics": graph_statistics(ctx, graph.id, graph=graph),
        "createdAt": datetime.utcnow().isoformat(),
    }


def publish_graph(ctx: AuthContext, graph_id: str, payload: PublishInput) -> dict:
    """发布图谱正式修订：冻结当前实体/关系为不可变快照并记录本体修订号。
    发布后 Bot 可绑定固定修订，历史答案可复现；后续再次发布不影响已绑定的快照。
    异常：NotFoundError、ValidationError、ConflictError。
    """
    graph = get_graph(ctx, graph_id, "graphs.publish")
    _version(graph, payload.version)
    snapshot = _snapshot(ctx, graph)
    if not snapshot["entities"]:
        raise ValidationError("图谱至少包含一个实体后才能发布")
    revision = (ctx.db.scalar(select(func.max(GraphRevision.revision)).where(
        GraphRevision.graph_id == graph.id,
    )) or 0) + 1
    ctx.db.add(GraphRevision(
        workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, graph_id=graph.id,
        revision=revision, snapshot=snapshot, reason=payload.reason.strip(),
    ))
    graph.status = "published"
    graph.published_revision = revision
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="graphs.publish", resource_type="knowledge_graph", resource_id=graph.id,
          metadata={"revision": revision})
    _commit(ctx, "图谱发布修订冲突，请刷新后重试")
    return graph_view(ctx, graph, detail=True)


def graph_revisions(ctx: AuthContext, graph_id: str) -> list[dict]:
    """列出图谱的历史正式修订（倒序）。异常：NotFoundError。"""
    graph = get_graph(ctx, graph_id, "graphs.view")
    rows = ctx.db.scalars(select(GraphRevision).where(
        GraphRevision.workspace_id == ctx.workspace_id, GraphRevision.graph_id == graph.id,
    ).order_by(GraphRevision.revision.desc()).limit(100)).all()
    return [{"id": row.id, "revision": row.revision, "reason": row.reason,
             "snapshot": row.snapshot, "createdAt": _iso(row.created_at)} for row in rows]


def remove_graph(ctx: AuthContext, graph_id: str, version: int) -> None:
    """归档/软删除图谱。已有 Bot 绑定固定修订时不删除数据（快照仍可读）。
    异常：NotFoundError、ConflictError（版本冲突或被引用）。
    """
    graph = get_graph(ctx, graph_id, "graphs.delete")
    _version(graph, version)
    from app.modules.knowledge_graph.models import BusinessBot
    bots = ctx.db.scalars(select(BusinessBot).where(
        BusinessBot.workspace_id == ctx.workspace_id, BusinessBot.status != "deleted",
    )).all()
    if any(graph.id in (bot.graph_ids or []) for bot in bots):
        raise ConflictError("图谱仍被 Bot 使用，请先解除绑定")
    graph.status = "deleted"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="graphs.delete", resource_type="knowledge_graph", resource_id=graph.id)
    _commit(ctx, "图谱删除失败")
