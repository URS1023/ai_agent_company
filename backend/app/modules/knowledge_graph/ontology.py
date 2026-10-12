"""Ontology lifecycle, concept/relation validation and immutable publication."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.common.audit import audit
from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.common.owned_records import owned_record, visible_records
from app.errors import ConflictError, NotFoundError, ValidationError
from app.modules.knowledge_graph.models import (
    GraphEntity,
    Ontology,
    OntologyConcept,
    OntologyRelationType,
    OntologyRevision,
)
from app.modules.knowledge_graph.schemas import ConceptInput, OntologyInput, PublishInput, RelationTypeInput


def _iso(value: datetime | None) -> str | None:
    """时间转 ISO 字符串；None 原样返回 None。"""
    return value.isoformat() if value else None


def _version(record, expected: int | None, *, creating: bool = False) -> None:
    """统一乐观锁校验：新建不得带 version，修改必须带且一致。异常：ValidationError / ConflictError。"""
    if creating:
        if expected is not None:
            raise ValidationError("新建记录不能提交 version")
        return
    if expected is None:
        raise ValidationError("修改记录必须提交 version")
    if record.version != expected:
        raise ConflictError("记录已被其他操作修改，请刷新后重试", current_version=record.version)


def _commit(ctx: AuthContext, duplicate_message: str) -> None:
    """提交事务；把唯一约束冲突翻译成 ConflictError（duplicate_message 为面向用户的文案）。"""
    try:
        ctx.db.commit()
    except IntegrityError as exc:
        ctx.db.rollback()
        raise ConflictError(duplicate_message) from exc
    except StaleDataError as exc:
        ctx.db.rollback()
        raise ConflictError("记录已被其他操作修改，请刷新后重试") from exc


def get_ontology(ctx: AuthContext, ontology_id: str, permission: str = "ontology.view") -> Ontology:
    """取本体并做归属/权限校验。异常：NotFoundError（不存在或越权）、PermissionError。"""
    row = owned_record(ctx, Ontology, ontology_id, permission)
    if row.status == "deleted":
        raise NotFoundError("本体模型不存在")
    return row


def ontology_view(ctx: AuthContext, row: Ontology, *, detail: bool = False) -> dict:
    """本体视图；detail=True 时才附带概念与关系类型明细。"""
    concepts = ctx.db.scalars(select(OntologyConcept).where(
        OntologyConcept.workspace_id == ctx.workspace_id,
        OntologyConcept.ontology_id == row.id,
        OntologyConcept.status != "deleted",
    ).order_by(OntologyConcept.created_at)).all()
    relations = ctx.db.scalars(select(OntologyRelationType).where(
        OntologyRelationType.workspace_id == ctx.workspace_id,
        OntologyRelationType.ontology_id == row.id,
        OntologyRelationType.status != "deleted",
    ).order_by(OntologyRelationType.created_at)).all()
    result = {
        "id": row.id, "name": row.name, "description": row.description,
        "status": row.status, "publishedRevision": row.published_revision,
        "version": row.version, "conceptCount": len(concepts), "relationCount": len(relations),
        "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
        "source": "智慧云脑 Cloud Brain",
    }
    if detail:
        result["concepts"] = [concept_view(item) for item in concepts]
        result["relationTypes"] = [relation_view(item) for item in relations]
        result["visualization"] = {
            "nodes": [{"id": item.id, "label": item.name, "code": item.code, "color": item.color} for item in concepts],
            "edges": [{"id": item.id, "source": item.source_concept_id, "target": item.target_concept_id,
                       "label": item.name, "directed": item.directed} for item in relations],
        }
    return result


def concept_view(row: OntologyConcept) -> dict:
    """本体概念的对外序列化（含属性 schema 与术语提示）。"""
    return {
        "id": row.id, "ontologyId": row.ontology_id, "code": row.code, "name": row.name,
        "description": row.description, "color": row.color, "icon": row.icon,
        "attributes": row.attributes, "recognitionTerms": row.recognition_terms,
        "constraints": row.constraints, "status": row.status, "version": row.version,
        "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
    }


def relation_view(row: OntologyRelationType) -> dict:
    """本体关系类型的对外序列化（含源/目标概念与基数）。"""
    return {
        "id": row.id, "ontologyId": row.ontology_id, "code": row.code, "name": row.name,
        "description": row.description, "sourceConceptId": row.source_concept_id,
        "targetConceptId": row.target_concept_id, "cardinality": row.cardinality,
        "directed": row.directed, "attributes": row.attributes, "constraints": row.constraints,
        "status": row.status, "version": row.version,
        "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
    }


def list_ontologies(ctx: AuthContext, keyword: str = "") -> list[dict]:
    """列出当前账号可见的本体（keyword 匹配名称/描述）。可见性由 visible_records 决定。"""
    statement = visible_records(ctx, Ontology, "ontology.view").where(Ontology.status != "deleted")
    clean = keyword.strip()
    if clean:
        statement = statement.where((Ontology.name.contains(clean)) | (Ontology.description.contains(clean)))
    rows = ctx.db.scalars(statement.order_by(Ontology.updated_at.desc()).limit(500)).all()
    return [ontology_view(ctx, row) for row in rows]


def save_ontology(ctx: AuthContext, payload: OntologyInput, ontology_id: str | None = None) -> dict:
    """新建或更新本体元信息（名称/描述），返回视图。
    bot_id 为空即新建（禁止带 version），否则按乐观锁更新。
    异常：ValidationError、NotFoundError、ConflictError（重名或并发）。
    """
    permission = "ontology.edit" if ontology_id else "ontology.create"
    require_permission(ctx, permission)
    if ontology_id:
        row = get_ontology(ctx, ontology_id, permission)
        _version(row, payload.version)
    else:
        _version(None, payload.version, creating=True)
        row = Ontology(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    row.name = payload.name.strip()
    row.description = payload.description.strip()
    if row.status in {"archived", "deleted"}:
        row.status = "draft"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="ontology.update" if ontology_id else "ontology.create",
          resource_type="ontology", resource_id=row.id)
    _commit(ctx, "本体模型名称已存在")
    return ontology_view(ctx, row, detail=True)


def remove_ontology(ctx: AuthContext, ontology_id: str, version: int) -> None:
    """软删除本体。若已有图谱/抽取任务引用该本体则拒绝删除，避免下游元模型悬空。
    异常：NotFoundError、ConflictError（被引用或版本冲突）。
    """
    row = get_ontology(ctx, ontology_id, "ontology.delete")
    _version(row, version)
    from app.modules.knowledge_graph.models import BusinessBot, ExtractionJob, KnowledgeGraph
    references = any((
        ctx.db.scalar(select(func.count(ExtractionJob.id)).where(ExtractionJob.ontology_id == row.id, ExtractionJob.status != "deleted")),
        ctx.db.scalar(select(func.count(KnowledgeGraph.id)).where(KnowledgeGraph.ontology_id == row.id, KnowledgeGraph.status != "deleted")),
    ))
    if references:
        raise ConflictError("本体仍被抽取任务或图谱引用，请先归档关联资源")
    row.status = "deleted"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="ontology.delete", resource_type="ontology", resource_id=row.id)
    _commit(ctx, "本体模型删除失败")


def _concept(ctx: AuthContext, ontology: Ontology, concept_id: str, permission: str) -> OntologyConcept:
    """按 ontology_id 取概念并校验归属，是概念写操作的统一入口。异常：NotFoundError。"""
    require_permission(ctx, permission)
    row = ctx.db.get(OntologyConcept, concept_id)
    if not row or row.workspace_id != ctx.workspace_id or row.ontology_id != ontology.id or row.status == "deleted":
        raise NotFoundError("概念不存在")
    return row


def save_concept(ctx: AuthContext, ontology_id: str, payload: ConceptInput, concept_id: str | None = None) -> dict:
    """新建或更新本体概念（名称、业务键映射、属性定义）。
    校验要点：概念名在本体内唯一；属性定义的 name/type 必填且不重复；删除被关系引用的概念会被拒绝。
    异常：ValidationError、NotFoundError、ConflictError。
    """
    permission = "ontology.edit" if concept_id else "ontology.create"
    ontology = get_ontology(ctx, ontology_id, permission)
    if concept_id:
        row = _concept(ctx, ontology, concept_id, permission)
        _version(row, payload.version)
    else:
        _version(None, payload.version, creating=True)
        row = OntologyConcept(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, ontology_id=ontology.id)
        ctx.db.add(row)
    row.code = payload.code.strip()
    row.name = payload.name.strip()
    row.description = payload.description.strip()
    row.color = payload.color
    row.icon = payload.icon.strip()
    row.attributes = [item.model_dump(by_alias=True) for item in payload.attributes]
    row.recognition_terms = payload.recognition_terms
    row.constraints = payload.constraints
    row.status = "active"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="ontology.concept.update" if concept_id else "ontology.concept.create",
          resource_type="ontology_concept", resource_id=row.id, metadata={"ontology_id": ontology.id})
    _commit(ctx, "同一本体中的概念编码不能重复")
    return concept_view(row)


def remove_concept(ctx: AuthContext, ontology_id: str, concept_id: str, version: int) -> None:
    """删除本体概念；被关系类型引用时拒绝（需先删关系），避免关系指向不存在的概念。
    异常：NotFoundError、ConflictError。
    """
    ontology = get_ontology(ctx, ontology_id, "ontology.delete")
    row = _concept(ctx, ontology, concept_id, "ontology.delete")
    _version(row, version)
    relation_count = ctx.db.scalar(select(func.count(OntologyRelationType.id)).where(
        OntologyRelationType.ontology_id == ontology.id,
        OntologyRelationType.status != "deleted",
        (OntologyRelationType.source_concept_id == row.id) | (OntologyRelationType.target_concept_id == row.id),
    )) or 0
    entity_count = ctx.db.scalar(select(func.count(GraphEntity.id)).where(
        GraphEntity.concept_id == row.id, GraphEntity.status != "deleted",
    )) or 0
    if relation_count or entity_count:
        raise ConflictError("概念仍被关系类型或图谱实体引用，不能删除")
    row.status = "deleted"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="ontology.concept.delete", resource_type="ontology_concept", resource_id=row.id)
    _commit(ctx, "概念删除失败")


def save_relation(ctx: AuthContext, ontology_id: str, payload: RelationTypeInput,
                  relation_id: str | None = None) -> dict:
    """新建或更新本体关系类型（source→target + 基数约束）。
    校验要点：源/目标概念必须属于同一本体且不能缺少；基数取值受限；不允许自环之外的非法组合。
    异常：ValidationError、NotFoundError、ConflictError。
    """
    permission = "ontology.edit" if relation_id else "ontology.create"
    ontology = get_ontology(ctx, ontology_id, permission)
    source = _concept(ctx, ontology, payload.source_concept_id, permission)
    target = _concept(ctx, ontology, payload.target_concept_id, permission)
    if relation_id:
        row = ctx.db.get(OntologyRelationType, relation_id)
        if not row or row.workspace_id != ctx.workspace_id or row.ontology_id != ontology.id or row.status == "deleted":
            raise NotFoundError("关系类型不存在")
        _version(row, payload.version)
    else:
        _version(None, payload.version, creating=True)
        row = OntologyRelationType(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, ontology_id=ontology.id)
        ctx.db.add(row)
    row.code = payload.code.strip()
    row.name = payload.name.strip()
    row.description = payload.description.strip()
    row.source_concept_id = source.id
    row.target_concept_id = target.id
    row.cardinality = payload.cardinality
    row.directed = payload.directed
    row.attributes = [item.model_dump(by_alias=True) for item in payload.attributes]
    row.constraints = payload.constraints
    row.status = "active"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="ontology.relation.update" if relation_id else "ontology.relation.create",
          resource_type="ontology_relation_type", resource_id=row.id, metadata={"ontology_id": ontology.id})
    _commit(ctx, "同一本体中的关系编码不能重复")
    return relation_view(row)


def remove_relation(ctx: AuthContext, ontology_id: str, relation_id: str, version: int) -> None:
    """删除本体关系类型；被图谱关系实例引用时拒绝。异常：NotFoundError、ConflictError。"""
    ontology = get_ontology(ctx, ontology_id, "ontology.delete")
    require_permission(ctx, "ontology.delete")
    row = ctx.db.get(OntologyRelationType, relation_id)
    if not row or row.workspace_id != ctx.workspace_id or row.ontology_id != ontology.id or row.status == "deleted":
        raise NotFoundError("关系类型不存在")
    _version(row, version)
    from app.modules.knowledge_graph.models import GraphRelation
    used = ctx.db.scalar(select(func.count(GraphRelation.id)).where(
        GraphRelation.relation_type_id == row.id, GraphRelation.status != "deleted",
    )) or 0
    if used:
        raise ConflictError("关系类型仍被图谱关系引用，不能删除")
    row.status = "deleted"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="ontology.relation.delete", resource_type="ontology_relation_type", resource_id=row.id)
    _commit(ctx, "关系类型删除失败")


def _snapshot(ctx: AuthContext, ontology: Ontology) -> dict:
    """生成本体发布快照（概念 + 关系类型的冻结定义）。"""
    detail = ontology_view(ctx, ontology, detail=True)
    return {
        "ontology": {key: detail[key] for key in ("id", "name", "description")},
        "concepts": detail["concepts"], "relationTypes": detail["relationTypes"],
        "createdAt": datetime.utcnow().isoformat(),
    }


def publish_ontology(ctx: AuthContext, ontology_id: str, payload: PublishInput) -> dict:
    """发布本体正式修订：冻结当前定义为不可变快照，并更新本体状态。
    发布后旧修订永远可读，图谱/抽取任务可绑定固定修订号以避免元模型漂移。
    异常：NotFoundError、ValidationError（无可发布内容）、ConflictError。
    """
    ontology = get_ontology(ctx, ontology_id, "ontology.publish")
    _version(ontology, payload.version)
    snapshot = _snapshot(ctx, ontology)
    if not snapshot["concepts"]:
        raise ValidationError("至少创建一个概念后才能发布本体")
    revision = (ctx.db.scalar(select(func.max(OntologyRevision.revision)).where(
        OntologyRevision.ontology_id == ontology.id,
    )) or 0) + 1
    row = OntologyRevision(
        workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, ontology_id=ontology.id,
        revision=revision, snapshot=snapshot, reason=payload.reason.strip(),
    )
    ctx.db.add(row)
    ontology.status = "published"
    ontology.published_revision = revision
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="ontology.publish", resource_type="ontology", resource_id=ontology.id,
          metadata={"revision": revision})
    _commit(ctx, "本体发布修订冲突，请刷新后重试")
    return ontology_view(ctx, ontology, detail=True)


def ontology_revisions(ctx: AuthContext, ontology_id: str) -> list[dict]:
    """列出本体的历史正式修订（倒序）。异常：NotFoundError。"""
    ontology = get_ontology(ctx, ontology_id, "ontology.view")
    rows = ctx.db.scalars(select(OntologyRevision).where(
        OntologyRevision.workspace_id == ctx.workspace_id,
        OntologyRevision.ontology_id == ontology.id,
    ).order_by(OntologyRevision.revision.desc()).limit(100)).all()
    return [{"id": row.id, "revision": row.revision, "reason": row.reason,
             "snapshot": row.snapshot, "createdAt": _iso(row.created_at)} for row in rows]


def published_snapshot(ctx: AuthContext, ontology_id: str, revision: int | None = None) -> tuple[Ontology, OntologyRevision]:
    """读取本体的正式修订快照；revision 为 None 时取当前发布版本。
    异常：ValidationError（本体未发布）、NotFoundError（修订不存在）。
    """
    ontology = get_ontology(ctx, ontology_id, "ontology.view")
    selected = revision or ontology.published_revision
    if selected is None:
        raise ValidationError("本体尚未发布正式版本")
    row = ctx.db.scalar(select(OntologyRevision).where(
        OntologyRevision.workspace_id == ctx.workspace_id,
        OntologyRevision.ontology_id == ontology.id,
        OntologyRevision.revision == selected,
    ))
    if not row:
        raise NotFoundError("本体正式修订不存在")
    return ontology, row
