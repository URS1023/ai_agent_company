"""按当前授权来源生成证据候选，并将固定运行的已确认内容原子写入图谱。"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.common.audit import audit
from app.common.authorization import require_permission, require_resource
from app.common.data_scope import bind_data_scope
from app.common.deps import AuthContext
from app.common.owned_records import owned_record, visible_records
from app.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.models import File, KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.modules.knowledge.models import KnowledgeChunkMetadata
from app.modules.knowledge_graph import graphs as graph_service
from app.modules.knowledge_graph import ontology as ontology_service
from app.modules.knowledge_graph import extraction_recovery as recovery
from app.modules.knowledge_graph.models import (
    ExtractionCandidate,
    ExtractionJob,
    ExtractionRun,
    GraphEntity,
    GraphRelation,
    KnowledgeGraph,
)
from app.modules.knowledge_graph.schemas import (
    CandidateBatchReview,
    ExtractionExecuteInput,
    ExtractionJobInput,
    ExtractionMaterializeInput,
    GraphEntityInput,
    GraphRelationInput,
    property_value_key,
)


_MISSING = object()
MAX_UNSTRUCTURED_CHUNKS = 5000
MAX_UNSTRUCTURED_CHARACTERS = 5_000_000


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


def get_job(ctx: AuthContext, job_id: str, permission: str = "extraction.view") -> ExtractionJob:
    """取抽取任务并做归属/权限校验。异常：NotFoundError（不存在或越权）、PermissionError。"""
    row = owned_record(ctx, ExtractionJob, job_id, permission)
    if row.status == "deleted":
        raise NotFoundError("抽取任务不存在")
    return row


def _get_run(ctx: AuthContext, job: ExtractionJob, run_id: str, *, lock: bool = False) -> ExtractionRun:
    """按任务和工作区读取运行；复核与物化先锁运行，防止状态和首次目标交错修改。"""
    statement = select(ExtractionRun).where(
        ExtractionRun.id == run_id, ExtractionRun.workspace_id == ctx.workspace_id, ExtractionRun.job_id == job.id,
    ).execution_options(populate_existing=True)
    if lock:
        statement = statement.with_for_update()
    row = ctx.db.scalar(statement)
    if row is None:
        raise NotFoundError("抽取运行不存在")
    return row


def _run_snapshot(ctx: AuthContext, run: ExtractionRun):
    """只解释运行当时固定的正式本体；缺失历史元数据时拒绝借当前任务猜测。"""
    snapshot = run.input_snapshot if isinstance(run.input_snapshot, dict) else {}
    ontology_id, revision = snapshot.get("ontologyId"), snapshot.get("ontologyRevision")
    if not isinstance(ontology_id, str) or not ontology_id or type(revision) is not int or revision < 1:
        raise ValidationError("抽取运行缺少有效的固定本体修订，不能复核或物化")
    _, published = ontology_service.published_snapshot(ctx, ontology_id, revision)
    return published


def _candidate_counts(ctx: AuthContext, run_id: str) -> dict[str, int]:
    """统计某次运行的候选数量（按 kind / 审核状态分组），供列表与详情展示。"""
    counts = {"pending": 0, "accepted": 0, "rejected": 0, "entities": 0, "relations": 0}
    rows = ctx.db.execute(select(
        ExtractionCandidate.kind,
        ExtractionCandidate.review_status,
        func.count(ExtractionCandidate.id),
    ).where(
        ExtractionCandidate.workspace_id == ctx.workspace_id,
        ExtractionCandidate.run_id == run_id,
    ).group_by(ExtractionCandidate.kind, ExtractionCandidate.review_status)).all()
    for kind, status, count in rows:
        counts[status] = counts.get(status, 0) + int(count)
        counts["entities" if kind == "entity" else "relations"] += int(count)
    return counts


def run_view(ctx: AuthContext, row: ExtractionRun) -> dict:
    """抽取运行的对外序列化（状态、计数、错误摘要、耗时）。"""
    return {
        "id": row.id,
        "jobId": row.job_id,
        "runNumber": row.run_number,
        "mode": row.mode,
        "status": row.status,
        "summary": {**(row.summary or {}), "review": _candidate_counts(ctx, row.id)},
        "events": row.events,
        "errorMessage": row.error_message,
        "startedAt": _iso(row.started_at),
        "completedAt": _iso(row.completed_at),
        "createdAt": _iso(row.created_at),
    }


def job_view(ctx: AuthContext, row: ExtractionJob, *, detail: bool = False) -> dict:
    """抽取任务的对外序列化；detail=True 时才附带来源与映射配置明细。"""
    result = {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "mode": row.mode,
        "ontologyId": row.ontology_id,
        "ontologyRevision": row.ontology_revision,
        "knowledgeBaseId": row.knowledge_base_id,
        "documentIds": row.document_ids,
        "mappings": row.mappings,
        "recordCount": len(row.input_records or []),
        "status": row.status,
        "executionCount": row.execution_count,
        "targetGraphId": row.target_graph_id,
        "resultSummary": row.result_summary,
        "errorMessage": row.error_message,
        "version": row.version,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
        "source": "智慧云脑 Cloud Brain",
    }
    if detail:
        result["records"] = row.input_records
        runs = ctx.db.scalars(select(ExtractionRun).where(
            ExtractionRun.workspace_id == ctx.workspace_id,
            ExtractionRun.job_id == row.id,
        ).order_by(ExtractionRun.run_number.desc()).limit(100)).all()
        result["runs"] = [run_view(ctx, item) for item in runs]
    return result


def candidate_view(row: ExtractionCandidate) -> dict:
    """抽取候选的对外序列化（kind、业务键、属性、审核状态与证据）。"""
    return {
        "id": row.id,
        "jobId": row.job_id,
        "runId": row.run_id,
        "kind": row.kind,
        "candidateKey": row.candidate_key,
        "conceptId": row.concept_id,
        "relationTypeId": row.relation_type_id,
        "payload": row.payload,
        "evidence": row.evidence,
        "reviewStatus": row.review_status,
        "reviewNote": row.review_note,
        "reviewedBy": row.reviewed_by,
        "reviewedAt": _iso(row.reviewed_at),
        "version": row.version,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
    }


def list_jobs(ctx: AuthContext, *, keyword: str = "", mode: str = "", status: str = "") -> list[dict]:
    """列出当前账号可见的抽取任务（keyword/mode/status 筛选）。可见性由 visible_records 决定。"""
    statement = visible_records(ctx, ExtractionJob, "extraction.view").where(ExtractionJob.status != "deleted")
    if keyword.strip():
        clean = keyword.strip()
        statement = statement.where((ExtractionJob.name.contains(clean)) | (ExtractionJob.description.contains(clean)))
    if mode:
        if mode not in {"structured", "unstructured"}:
            raise ValidationError("未知抽取模式")
        statement = statement.where(ExtractionJob.mode == mode)
    if status:
        statement = statement.where(ExtractionJob.status == status)
    rows = ctx.db.scalars(statement.order_by(ExtractionJob.updated_at.desc()).limit(500)).all()
    return [job_view(ctx, row) for row in rows]


def _knowledge_source(ctx: AuthContext, knowledge_base_id: str, document_ids: list[str]) -> KnowledgeBase:
    """校验并返回抽取任务的知识来源知识库（要求可见且 active），供来源展示与分块加载。
    异常：NotFoundError（知识库不存在或无权访问）。
    """
    require_permission(ctx, "knowledge.view")
    kb = ctx.db.scalar(select(KnowledgeBase).where(
        KnowledgeBase.id == knowledge_base_id, KnowledgeBase.workspace_id == ctx.workspace_id,
    ).execution_options(populate_existing=True))
    if not kb or kb.workspace_id != ctx.workspace_id or kb.status != "active":
        raise NotFoundError("知识库不存在或不可用")
    require_resource(ctx, "knowledge.view", kb)
    if len(document_ids) != len(set(document_ids)):
        raise ValidationError("文档不能重复选择")
    if document_ids:
        rows = ctx.db.scalars(select(KnowledgeDocument).where(
            KnowledgeDocument.workspace_id == ctx.workspace_id,
            KnowledgeDocument.knowledge_base_id == kb.id,
            KnowledgeDocument.id.in_(document_ids),
        ).execution_options(populate_existing=True)).all()
        if {row.id for row in rows} != set(document_ids):
            raise ValidationError("所选文档不存在、无权限或不属于该知识库")
        unavailable = [row.id for row in rows if row.status != "ready"]
        if unavailable:
            raise ValidationError("所选文档尚未完成解析", document_ids=unavailable)
    return kb


def save_job(ctx: AuthContext, payload: ExtractionJobInput, job_id: str | None = None) -> dict:
    """新建或更新抽取任务（来源知识库、抽取模式、字段映射）。
    校验要点：知识库必须可见；结构化模式必须给实体/关系映射；模式取值受限（structured/unstructured 等）。
    异常：ValidationError、NotFoundError、ConflictError。
    """
    permission = "extraction.edit" if job_id else "extraction.create"
    require_permission(ctx, permission)
    _, ontology_revision = ontology_service.published_snapshot(ctx, payload.ontology_id)
    if payload.mode == "unstructured":
        _knowledge_source(ctx, payload.knowledge_base_id or "", payload.document_ids)
    if job_id:
        row = get_job(ctx, job_id, permission)
        _version(row, payload.version)
        if row.status == "running":
            raise ConflictError("抽取任务正在执行，不能修改")
    else:
        _version(None, payload.version, creating=True)
        row = ExtractionJob(workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id)
        ctx.db.add(row)
    row.name = payload.name.strip()
    row.description = payload.description.strip()
    row.mode = payload.mode
    row.ontology_id = payload.ontology_id
    row.ontology_revision = ontology_revision.revision
    row.knowledge_base_id = payload.knowledge_base_id
    row.document_ids = list(payload.document_ids)
    row.mappings = payload.mappings.model_dump(by_alias=True)
    row.input_records = payload.records
    row.status = "draft"
    row.target_graph_id = None
    row.result_summary = {}
    row.error_message = ""
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="extraction.update" if job_id else "extraction.create",
          resource_type="extraction_job", resource_id=row.id,
          metadata={"mode": row.mode, "ontology_revision": row.ontology_revision})
    _commit(ctx, "抽取任务保存失败")
    return job_view(ctx, row, detail=True)


def remove_job(ctx: AuthContext, job_id: str, version: int) -> None:
    """软删除抽取任务（保留运行与候选历史）。异常：NotFoundError、ConflictError。"""
    row = get_job(ctx, job_id, "extraction.delete")
    _version(row, version)
    if row.status == "running":
        raise ConflictError("抽取任务正在执行，不能删除")
    row.status = "deleted"
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="extraction.delete", resource_type="extraction_job", resource_id=row.id)
    _commit(ctx, "抽取任务删除失败")


def _field(record: Any, path: str) -> Any:
    """按点分路径从 dict/对象里取值；取不到返回 None（结构化解析的容错读取）。"""
    current = record
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def _required_field(record: dict, path: str, *, row_number: int, label: str) -> Any:
    """按点分路径取必填字段，缺失时抛 ValidationError 并带上行号与字段标签，
    让用户在导入报错里直接定位到第几行少哪个字段。
    """
    value = _field(record, path)
    if value is _MISSING or value is None or value == "":
        raise ValidationError(f"第 {row_number} 条记录缺少{label}字段：{path}")
    return value


def _entity_key(concept_id: str, business_key: str) -> str:
    """生成候选实体的稳定键：concept_id + business_key，用于去重与物化定位。"""
    digest = hashlib.sha256((concept_id + "\0" + business_key).encode("utf-8")).hexdigest()[:32]
    return f"entity:{concept_id}:{digest}"


def _relation_key(relation_type_id: str, source_key: str, target_key: str) -> str:
    """生成候选关系的稳定键：关系类型 + 源键 + 目标键，用于去重与物化定位。"""
    digest = hashlib.sha256((relation_type_id + "\0" + source_key + "\0" + target_key).encode("utf-8")).hexdigest()
    return f"relation:{relation_type_id}:{digest[:32]}"


def _definition_maps(snapshot: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """从本体快照里拆出 (概念定义表, 关系类型定义表) 两个按 id 索引的字典，供抽取时校验与取值。"""
    concepts = {item["id"]: item for item in snapshot.get("concepts", [])}
    relations = {item["id"]: item for item in snapshot.get("relationTypes", [])}
    return concepts, relations


def _mapped_properties(record: dict, mappings: dict[str, str]) -> dict:
    """按映射把原始记录的字段搬成实体属性值（未配置映射的字段不搬运）。"""
    values = {}
    for property_name, source_path in mappings.items():
        value = _field(record, source_path)
        if value is not _MISSING:
            values[property_name] = value
    return values


def _merge_evidence(spec: dict, evidence: dict) -> None:
    """合并多次命中的证据（同键候选的证据累加去重），保证候选可追溯到全部来源。"""
    matches = spec["evidence"].setdefault("matches", [])
    if evidence not in matches:
        matches.append(evidence)


def _structured_specs(snapshot: dict, records: list[dict], mappings: dict) -> list[dict]:
    """把结构化记录（表格/JSON）转成候选实体与关系规格。
    要点：按映射抽取字段、生成稳定键去重、合并证据；缺少必填字段按行报错而不是静默跳过。
    """
    concepts, relations = _definition_maps(snapshot)
    entities: dict[str, dict] = {}
    relation_specs: dict[str, dict] = {}
    entity_mappings = mappings.get("entityMappings", [])
    relation_mappings = mappings.get("relationMappings", [])
    for mapping in entity_mappings:
        if mapping.get("conceptId") not in concepts:
            raise ValidationError("实体映射引用了固定本体修订中不存在的概念")
    for mapping in relation_mappings:
        definition = relations.get(mapping.get("relationTypeId"))
        if not definition:
            raise ValidationError("关系映射引用了固定本体修订中不存在的关系类型")
        actual = (mapping.get("sourceConceptId"), mapping.get("targetConceptId"))
        expected = (definition.get("sourceConceptId"), definition.get("targetConceptId"))
        if actual != expected and not (not definition.get("directed", True) and actual == tuple(reversed(expected))):
            raise ValidationError("关系映射的起点、终点概念与本体定义不一致")

    for row_number, record in enumerate(records, start=1):
        for mapping in entity_mappings:
            concept_id = mapping["conceptId"]
            raw_key = _required_field(record, mapping["keyField"], row_number=row_number, label="业务键")
            raw_name = _required_field(record, mapping["nameField"], row_number=row_number, label="名称")
            business_key = str(raw_key).strip()
            name = str(raw_name).strip()
            if len(business_key) > 300 or len(name) > 300:
                raise ValidationError(f"第 {row_number} 条记录的业务键或名称超过 300 个字符")
            properties = _mapped_properties(record, mapping.get("propertyFields", {}))
            graph_service._validate_properties(concepts[concept_id].get("attributes", []), properties, "抽取实体")
            key = _entity_key(concept_id, business_key)
            payload = {
                "candidateKey": key,
                "conceptId": concept_id,
                "businessKey": business_key,
                "name": name,
                "properties": properties,
            }
            evidence = {
                "rowNumber": row_number,
                "fields": sorted({mapping["keyField"], mapping["nameField"], *mapping.get("propertyFields", {}).values()}),
            }
            if key in entities:
                existing_payload = entities[key]["payload"]
                if any(existing_payload[item] != payload[item] for item in ("name", "properties")):
                    raise ValidationError(f"业务键 {business_key} 在多条记录中的名称或属性不一致")
                _merge_evidence(entities[key], evidence)
            else:
                entities[key] = {
                    "kind": "entity", "candidateKey": key, "conceptId": concept_id,
                    "relationTypeId": None, "payload": payload,
                    "evidence": {"source": "structured", "matches": [evidence]},
                }

        for mapping in relation_mappings:
            relation_type_id = mapping["relationTypeId"]
            source_value = str(_required_field(
                record, mapping["sourceKeyField"], row_number=row_number, label="关系起点",
            )).strip()
            target_value = str(_required_field(
                record, mapping["targetKeyField"], row_number=row_number, label="关系终点",
            )).strip()
            source_key = _entity_key(mapping["sourceConceptId"], source_value)
            target_key = _entity_key(mapping["targetConceptId"], target_value)
            properties = _mapped_properties(record, mapping.get("propertyFields", {}))
            graph_service._validate_properties(relations[relation_type_id].get("attributes", []), properties, "抽取关系")
            key = _relation_key(relation_type_id, source_key, target_key)
            payload = {
                "candidateKey": key,
                "relationTypeId": relation_type_id,
                "sourceCandidateKey": source_key,
                "targetCandidateKey": target_key,
                "properties": properties,
            }
            evidence = {
                "rowNumber": row_number,
                "fields": sorted({mapping["sourceKeyField"], mapping["targetKeyField"], *mapping.get("propertyFields", {}).values()}),
            }
            if key in relation_specs:
                if relation_specs[key]["payload"]["properties"] != properties:
                    raise ValidationError("同一起点和终点的关系属性不一致")
                _merge_evidence(relation_specs[key], evidence)
            else:
                relation_specs[key] = {
                    "kind": "relation", "candidateKey": key, "conceptId": None,
                    "relationTypeId": relation_type_id, "payload": payload,
                    "evidence": {"source": "structured", "matches": [evidence]},
                }

    dangling = [spec for spec in relation_specs.values()
                if spec["payload"]["sourceCandidateKey"] not in entities
                or spec["payload"]["targetCandidateKey"] not in entities]
    if dangling:
        raise ValidationError("关系字段映射未找到对应的实体候选，请补充相同概念与业务键的实体映射")
    return [*entities.values(), *relation_specs.values()]


def _excerpt(text: str, term: str) -> str:
    """截取包含关键词的上下文片段（带省略边界），让候选证据在列表里可直接阅读。"""
    position = text.casefold().find(term.casefold())
    if position < 0:
        return text[:240]
    start = max(0, position - 100)
    end = min(len(text), position + len(term) + 140)
    return text[start:end]


def _unstructured_specs(snapshot: dict, chunks: list[KnowledgeChunk]) -> list[dict]:
    """把知识分块文本转成候选实体/关系规格：按概念与关系类型的名称/别名做字面匹配，
    并把命中处的上下文片段作为证据。这是无标注文本的启发式抽取（非模型抽取）。
    """
    concepts, relations = _definition_maps(snapshot)
    entities: dict[str, dict] = {}
    relation_specs: dict[str, dict] = {}
    for chunk in chunks:
        folded = chunk.text.casefold()
        hits: dict[str, list[str]] = {}
        for concept_id, concept in concepts.items():
            terms = [str(term).strip() for term in concept.get("recognitionTerms", []) if str(term).strip()]
            matched = [term for term in terms if term.casefold() in folded]
            if not matched:
                continue
            hits[concept_id] = matched
            for term in matched:
                key = _entity_key(concept_id, term.casefold())
                evidence = {
                    "chunkId": chunk.id,
                    "documentId": chunk.document_id,
                    "ordinal": chunk.ordinal,
                    "location": chunk.location or {},
                    "term": term,
                    "excerpt": _excerpt(chunk.text, term),
                }
                if key in entities:
                    _merge_evidence(entities[key], evidence)
                else:
                    entities[key] = {
                        "kind": "entity", "candidateKey": key, "conceptId": concept_id,
                        "relationTypeId": None,
                        "payload": {
                            "candidateKey": key, "conceptId": concept_id,
                            "businessKey": term.casefold(), "name": term, "properties": {},
                        },
                        "evidence": {"source": "unstructured", "method": "recognition_term", "matches": [evidence]},
                    }
        for relation_type_id, definition in relations.items():
            source_concept = definition.get("sourceConceptId")
            target_concept = definition.get("targetConceptId")
            if source_concept not in hits or target_concept not in hits:
                continue
            for source_term in hits[source_concept]:
                for target_term in hits[target_concept]:
                    source_key = _entity_key(source_concept, source_term.casefold())
                    target_key = _entity_key(target_concept, target_term.casefold())
                    key = _relation_key(relation_type_id, source_key, target_key)
                    evidence = {
                        "chunkId": chunk.id,
                        "documentId": chunk.document_id,
                        "ordinal": chunk.ordinal,
                        "location": chunk.location or {},
                        "sourceTerm": source_term,
                        "targetTerm": target_term,
                        "excerpt": _excerpt(chunk.text, source_term),
                    }
                    if key in relation_specs:
                        _merge_evidence(relation_specs[key], evidence)
                    else:
                        relation_specs[key] = {
                            "kind": "relation", "candidateKey": key, "conceptId": None,
                            "relationTypeId": relation_type_id,
                            "payload": {
                                "candidateKey": key, "relationTypeId": relation_type_id,
                                "sourceCandidateKey": source_key, "targetCandidateKey": target_key,
                                "properties": {},
                            },
                            "evidence": {
                                "source": "unstructured", "method": "co_occurrence_candidate",
                                "matches": [evidence],
                            },
                        }
    return [*entities.values(), *relation_specs.values()]


@contextmanager
def _knowledge_scope(ctx: AuthContext):
    """知识授权只用于当前来源读取；成功、异常和嵌套调用均恢复原动作范围。"""
    keys = ("resource_access_criteria", "actor_user_id", "actor_workspace_id")
    previous = {key: ctx.db.info.get(key, _MISSING) for key in keys}
    try:
        require_permission(ctx, "knowledge.view")
        bind_data_scope(ctx, "knowledge.view")
        yield
    finally:
        for key, value in previous.items():
            if value is _MISSING:
                ctx.db.info.pop(key, None)
            else:
                ctx.db.info[key] = value


def _load_chunks(ctx: AuthContext, job: ExtractionJob) -> list[KnowledgeChunk]:
    """在知识作用域内读取当前完整来源；抽取统一要求文档 ready，过滤后再计算上限。"""
    with _knowledge_scope(ctx):
        _knowledge_source(ctx, job.knowledge_base_id or "", list(job.document_ids or []))
        statement = (select(KnowledgeChunk)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeChunk.knowledge_base_id)
            .join(File, File.id == KnowledgeDocument.file_id)
            .outerjoin(KnowledgeChunkMetadata, KnowledgeChunkMetadata.chunk_id == KnowledgeChunk.id)
            .where(
                KnowledgeChunk.workspace_id == ctx.workspace_id,
                KnowledgeChunk.knowledge_base_id == job.knowledge_base_id,
                KnowledgeDocument.workspace_id == ctx.workspace_id,
                KnowledgeDocument.knowledge_base_id == KnowledgeChunk.knowledge_base_id,
                KnowledgeDocument.status == "ready",
                KnowledgeBase.workspace_id == ctx.workspace_id, KnowledgeBase.status == "active",
                File.workspace_id == ctx.workspace_id, File.status == "ready", File.deleted_at.is_(None),
                KnowledgeChunkMetadata.id.is_(None) | (
                    (KnowledgeChunkMetadata.workspace_id == ctx.workspace_id)
                    & (KnowledgeChunkMetadata.knowledge_base_id == KnowledgeChunk.knowledge_base_id)
                    & KnowledgeChunkMetadata.enabled.is_(True)),
            ).execution_options(populate_existing=True))
        if job.document_ids:
            statement = statement.where(KnowledgeChunk.document_id.in_(job.document_ids))
        chunks = ctx.db.scalars(statement.order_by(KnowledgeChunk.document_id, KnowledgeChunk.ordinal, KnowledgeChunk.id)
                                .limit(MAX_UNSTRUCTURED_CHUNKS + 1)).all()
    if len(chunks) > MAX_UNSTRUCTURED_CHUNKS:
        raise ValidationError(f"单次抽取最多读取 {MAX_UNSTRUCTURED_CHUNKS} 个知识分块，请选择更少文档")
    total = sum(len(chunk.text) for chunk in chunks)
    if total > MAX_UNSTRUCTURED_CHARACTERS:
        raise ValidationError("单次抽取文本超过 500 万字符，请缩小文档范围")
    if not chunks:
        raise ValidationError("所选知识库文档没有可抽取的文本分块")
    return chunks


def _record_failure(bind, workspace_id: str, user_id: str, job_id: str, run_id: str,
                    generation: str, message: str) -> bool:
    """独立事务条件收尾；同代次已成功仅返回存在标记，不覆写终态或重做执行。"""
    with Session(bind=bind, expire_on_commit=False) as db:
        job, run = recovery.locked_pair(db, run_id, workspace_id)
        if (job is None or job.id != job_id or recovery.owner_of(run).get("generation") != generation):
            return False
        if run.status in {"review", "completed"}:
            return True
        if not recovery.current_run(job, run, generation):
            return False
        recovery.mark_failed(db, job, run, message, actor_user_id=user_id)
        try:
            db.commit()
        except StaleDataError:
            db.rollback()
    return False


def _committed_result(bind, token: str, workspace_id: str, job_id: str, run_id: str, generation: str) -> dict | None:
    """异常后重新认证、授权并读取成功；无法重新认证的内部调用不冒充已恢复响应。"""
    from starlette.requests import Request
    from app.common.deps import get_current_context
    with Session(bind=bind, expire_on_commit=False) as db:
        request = Request({"type": "http", "method": "POST", "scheme": "http", "server": ("localhost", 0),
            "path": f"/api/v1/extractions/{job_id}/execute", "query_string": b"",
            "headers": [(b"authorization", ("Bearer " + token).encode("latin-1"))]})
        # 认证可能提交令牌心跳，因此必须在业务查询前、独立会话中执行。
        fresh = get_current_context(request, db=db, x_workspace_id=workspace_id, credentials=None)
        job = get_job(fresh, job_id, "extraction.execute")
        run = _get_run(fresh, job, run_id)
        if (recovery.owner_of(run).get("generation") != generation or run.status not in {"review", "completed"}
                or not isinstance(job.result_summary, dict) or job.result_summary.get("runId") != run.id
                or job.status not in {"review", "completed"}):
            return None
        return {"job": job_view(fresh, job), "run": run_view(fresh, run)}


def execute_job(ctx: AuthContext, job_id: str, payload: ExtractionExecuteInput) -> dict:
    """执行一次抽取任务：按模式生成候选并落库，返回运行记录视图。
    要点：每次执行都会新建一条 ExtractionRun；候选只入库不直接进图谱，必须经人工审核（review）后物化。
    异常：ValidationError（任务配置非法）、NotFoundError（任务或来源不存在）。
    副作用：写 extraction_runs 与 extraction_candidates、更新任务状态，并写审计。
    """
    job = get_job(ctx, job_id, "extraction.execute")
    _version(job, payload.version)
    if job.status == "running":
        raise ConflictError("抽取任务已在执行")
    _, ontology_revision = ontology_service.published_snapshot(ctx, job.ontology_id, job.ontology_revision)
    run_number = (ctx.db.scalar(select(func.max(ExtractionRun.run_number)).where(
        ExtractionRun.job_id == job.id,
    )) or 0) + 1
    now = datetime.utcnow()
    owner = recovery.new_owner()
    input_snapshot = {
        recovery.OWNER_KEY: owner,
        "mode": job.mode,
        "ontologyId": job.ontology_id,
        "ontologyRevision": job.ontology_revision,
        "knowledgeBaseId": job.knowledge_base_id,
        "documentIds": job.document_ids,
        "mappings": job.mappings,
        "recordCount": len(job.input_records or []),
    }
    run = ExtractionRun(
        workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id, job_id=job.id,
        run_number=run_number, mode=job.mode, status="running", input_snapshot=input_snapshot,
        summary={}, events=[{"at": now.isoformat(), "type": "started", "message": "开始读取已授权来源"}],
        error_message="", started_at=now,
    )
    ctx.db.add(run)
    job.status = "running"
    job.execution_count += 1
    job.error_message = ""
    ctx.db.flush()
    job.result_summary = {"status": "running", "runId": run.id}
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="extraction.execute", resource_type="extraction_job", resource_id=job.id,
          metadata={"extractionRunId": run.id, "mode": job.mode, "run_number": run_number})
    # 标量身份在任何提交前固定；异常后不借已过期 ORM 对象猜测原调用。
    job_id, run_id, generation = job.id, run.id, owner["generation"]
    workspace_id, user_id, token, bind = ctx.workspace_id, ctx.user_id, ctx.token, ctx.db.get_bind()
    try:
        _commit(ctx, "抽取运行编号冲突，请刷新后重试")
        if job.mode == "structured":
            specs = _structured_specs(ontology_revision.snapshot, list(job.input_records or []), job.mappings or {})
            source_summary = {"records": len(job.input_records or [])}
        else:
            chunks = _load_chunks(ctx, job)
            specs = _unstructured_specs(ontology_revision.snapshot, chunks)
            source_summary = {"chunks": len(chunks), "documents": len({chunk.document_id for chunk in chunks})}
        entity_count = sum(item["kind"] == "entity" for item in specs)
        relation_count = len(specs) - entity_count
        job, run = recovery.locked_pair(ctx.db, run_id, workspace_id)
        if not recovery.current_run(job, run, generation):
            raise ConflictError("抽取运行归属已改变，不能提交旧运行结果")
        for item in specs:
            ctx.db.add(ExtractionCandidate(
                workspace_id=ctx.workspace_id,
                owner_user_id=ctx.user_id,
                job_id=job.id,
                run_id=run.id,
                kind=item["kind"],
                candidate_key=item["candidateKey"],
                concept_id=item["conceptId"],
                relation_type_id=item["relationTypeId"],
                payload=item["payload"],
                evidence=item["evidence"],
                review_status="pending",
                review_note="",
            ))
        completed = datetime.utcnow()
        summary = {**source_summary, "entities": entity_count, "relations": relation_count, "candidates": len(specs)}
        run.status = "review"
        run.summary = summary
        run.completed_at = completed
        run.events = [
            *run.events,
            {"at": completed.isoformat(), "type": "candidates_created", "message": f"生成 {len(specs)} 个待复核候选"},
        ]
        job.status = "review"
        job.result_summary = {**summary, "runId": run.id, "pending": len(specs)}
        audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
              action="extraction.candidates.created", resource_type="extraction_run", resource_id=run.id,
              metadata={**summary, "extractionRunId": run.id})
        _commit(ctx, "抽取候选存在重复键，请检查字段映射")
        return {"job": job_view(ctx, job), "run": run_view(ctx, run)}
    except Exception as exc:
        ctx.db.rollback()
        message = exc.message if isinstance(exc, AppError) else str(exc) or "抽取执行失败"
        if _record_failure(bind, workspace_id, user_id, job_id, run_id, generation, message):
            restored = _committed_result(bind, token, workspace_id, job_id, run_id, generation)
            if restored is not None:
                return restored
        raise


def list_runs(ctx: AuthContext, job_id: str) -> list[dict]:
    """列出该抽取任务的历史运行记录（倒序）。异常：NotFoundError。"""
    job = get_job(ctx, job_id, "extraction.view")
    rows = ctx.db.scalars(select(ExtractionRun).where(
        ExtractionRun.workspace_id == ctx.workspace_id,
        ExtractionRun.job_id == job.id,
    ).order_by(ExtractionRun.run_number.desc()).limit(200)).all()
    return [run_view(ctx, row) for row in rows]


def list_candidates(ctx: AuthContext, job_id: str, run_id: str, *, kind: str = "",
                    review_status: str = "", limit: int = 200, offset: int = 0) -> list[dict]:
    """分页/筛选查询某次运行的抽取候选（kind、reviewStatus）。异常：NotFoundError。"""
    job = get_job(ctx, job_id, "extraction.view")
    run = _get_run(ctx, job, run_id)
    statement = select(ExtractionCandidate).where(
        ExtractionCandidate.workspace_id == ctx.workspace_id,
        ExtractionCandidate.run_id == run.id,
    )
    if kind:
        if kind not in {"entity", "relation"}:
            raise ValidationError("未知候选类型")
        statement = statement.where(ExtractionCandidate.kind == kind)
    if review_status:
        if review_status not in {"pending", "accepted", "rejected"}:
            raise ValidationError("未知复核状态")
        statement = statement.where(ExtractionCandidate.review_status == review_status)
    rows = ctx.db.scalars(statement.order_by(ExtractionCandidate.kind, ExtractionCandidate.created_at)
                          .offset(offset).limit(limit)).all()
    return [candidate_view(row) for row in rows]


def _validate_review_payload(candidate: ExtractionCandidate, payload: dict, *, snapshot: dict | None = None) -> dict:
    """校验单条候选的审核入参（通过/驳回 + 允许审核时修正业务键与属性）。
    异常：ValidationError（状态非法或修正内容不符合概念 schema）。
    """
    if not isinstance(payload, dict):
        raise ValidationError("候选 payload 必须是对象")
    if payload.get("candidateKey") not in {None, candidate.candidate_key}:
        raise ValidationError("复核不能修改候选稳定键")
    if candidate.kind == "entity":
        if payload.get("conceptId") != candidate.concept_id:
            raise ValidationError("复核不能修改候选所属概念")
        business_key = str(payload.get("businessKey", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not business_key or not name:
            raise ValidationError("接受的实体候选必须包含业务键和名称")
        if len(business_key) > 300 or len(name) > 300:
            raise ValidationError("实体候选的业务键和名称最多 300 个字符")
        if not isinstance(payload.get("properties", {}), dict):
            raise ValidationError("实体候选 properties 必须是对象")
    else:
        if payload.get("relationTypeId") != candidate.relation_type_id:
            raise ValidationError("复核不能修改候选关系类型")
        if not payload.get("sourceCandidateKey") or not payload.get("targetCandidateKey"):
            raise ValidationError("接受的关系候选必须包含起点和终点候选键")
        if not isinstance(payload.get("properties", {}), dict):
            raise ValidationError("关系候选 properties 必须是对象")
    if snapshot is not None:
        concepts, relations = _definition_maps(snapshot)
        definition = (concepts.get(candidate.concept_id) if candidate.kind == "entity"
                      else relations.get(candidate.relation_type_id))
        if definition is None:
            raise ValidationError("候选类型不属于运行固定的本体修订")
        graph_service._validate_properties(definition.get("attributes", []), payload.get("properties", {}), "候选")
    if candidate.kind == "entity":
        return {**payload, "businessKey": business_key, "name": name}
    return payload


def _same_properties(definition: dict, existing: dict, proposed: dict) -> bool:
    """按固定类型比较业务值；保留缺省与 null 区别，拒绝以布尔/数字宽松相等冒充幂等。"""
    if not isinstance(existing, dict) or set(existing) != set(proposed):
        return False
    try:
        attributes = graph_service._validate_properties(definition.get("attributes", []), existing, "已有图谱记录")
        for key, value in existing.items():
            expected = proposed[key]
            if value is None or expected is None:
                if value is not expected:
                    return False
            elif property_value_key(attributes[key].type, value) != property_value_key(attributes[key].type, expected):
                return False
        return True
    except (ValidationError, ValueError, TypeError):
        return False


def review_candidates(ctx: AuthContext, job_id: str, run_id: str, payload: CandidateBatchReview) -> dict:
    """批量审核候选（通过/驳回），返回审核结果统计。
    要点：只有审核通过的候选才允许物化；审核结果与修正值会写回候选行，形成人工确认痕迹。
    异常：ValidationError、NotFoundError、ConflictError。
    """
    job = get_job(ctx, job_id, "extraction.edit")
    run = _get_run(ctx, job, run_id, lock=True)
    if run.status != "review":
        raise ConflictError("当前运行不处于可复核状态")
    snapshot = _run_snapshot(ctx, run).snapshot if any(item.decision == "accepted" for item in payload.items) else None
    ids = [item.candidate_id for item in payload.items]
    if len(ids) != len(set(ids)):
        raise ValidationError("同一批次不能重复提交候选")
    rows = ctx.db.scalars(select(ExtractionCandidate).where(
        ExtractionCandidate.workspace_id == ctx.workspace_id,
        ExtractionCandidate.run_id == run.id,
        ExtractionCandidate.id.in_(ids),
    ).order_by(ExtractionCandidate.id).with_for_update().execution_options(populate_existing=True)).all()
    by_id = {row.id: row for row in rows}
    if set(ids) != set(by_id):
        raise NotFoundError("一个或多个候选不存在")
    validated: list[tuple[ExtractionCandidate, Any, dict | None]] = []
    for item in payload.items:
        row = by_id[item.candidate_id]
        _version(row, item.version)
        replacement = _validate_review_payload(row, item.payload) if item.payload is not None else None
        if item.decision == "accepted":
            confirmed = _validate_review_payload(row, replacement if replacement is not None else row.payload, snapshot=snapshot)
            if replacement is not None:
                replacement = confirmed
        validated.append((row, item, replacement))
    reviewed_at = datetime.utcnow()
    for row, item, replacement in validated:
        if replacement is not None:
            row.payload = replacement
        row.review_status = item.decision
        row.review_note = item.note.strip()
        row.reviewed_by = ctx.user_id
        row.reviewed_at = reviewed_at
    ctx.db.flush()
    counts = _candidate_counts(ctx, run.id)
    run.summary = {**(run.summary or {}), "review": counts}
    if isinstance(job.result_summary, dict) and job.result_summary.get("runId") == run.id:
        job.result_summary = {**job.result_summary, "review": counts, "pending": counts["pending"]}
    audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
          action="extraction.candidates.review", resource_type="extraction_run", resource_id=run.id,
          metadata={"extractionRunId": run.id, "count": len(rows), "review": counts})
    _commit(ctx, "候选已被其他操作修改，请刷新后重试")
    return {"run": run_view(ctx, run), "items": [candidate_view(by_id[item.candidate_id]) for item in payload.items]}


def materialize(ctx: AuthContext, job_id: str, payload: ExtractionMaterializeInput) -> dict:
    """把审核通过的候选写入目标图谱（实体 + 关系），返回物化统计。
    要点：以候选的稳定键做幂等写入（同一候选重复物化不会产生重复实体）；
    未审核通过的候选会被跳过；本体与图谱的 schema 校验同样在此生效。
    异常：ValidationError（目标图谱/本体不匹配）、NotFoundError、ConflictError。
    """
    job = get_job(ctx, job_id, "extraction.execute")
    run = _get_run(ctx, job, payload.run_id, lock=True)
    if run.status not in {"review", "completed"}:
        raise ConflictError("只有已生成候选的运行可以写入图谱")
    # 同运行先串行，再锁目标图谱；全复用路径也必须走父锁，版本校验不能使用加锁前的缓存。
    graph = graph_service._lock_graph_write(ctx, payload.graph_id, "graphs.edit")
    _version(graph, payload.graph_version)
    if run.status == "completed":
        materialization = run.summary.get("materialization") if isinstance(run.summary, dict) else None
        if not isinstance(materialization, dict) or not materialization.get("graphId"):
            raise ConflictError("该运行缺少首次物化目标，不能推断或更换目标")
        if materialization["graphId"] != graph.id:
            raise ConflictError("该运行已写入其他目标图谱，不能事后更换目标")
    ontology_revision = _run_snapshot(ctx, run)
    if graph.ontology_id != ontology_revision.ontology_id or graph.ontology_revision != ontology_revision.revision:
        raise ValidationError("目标图谱与抽取运行固定使用的本体修订不一致")
    snapshot = ontology_revision.snapshot
    concepts, relations = _definition_maps(snapshot)
    all_candidates = ctx.db.scalars(select(ExtractionCandidate).where(
        ExtractionCandidate.workspace_id == ctx.workspace_id,
        ExtractionCandidate.run_id == run.id,
    ).order_by(ExtractionCandidate.kind, ExtractionCandidate.created_at, ExtractionCandidate.id)
        .with_for_update().execution_options(populate_existing=True)).all()
    if any(row.review_status == "pending" for row in all_candidates):
        raise ValidationError("请先接受或驳回所有待复核候选，再写入图谱")
    candidates = [row for row in all_candidates if row.review_status == "accepted"]
    entity_candidates = [row for row in candidates if row.kind == "entity"]
    relation_candidates = [row for row in candidates if row.kind == "relation"]
    if not entity_candidates and not relation_candidates:
        raise ValidationError("没有已接受的候选可写入图谱")
    accepted_entity_keys = {row.candidate_key for row in entity_candidates}
    for row in relation_candidates:
        source_key = row.payload.get("sourceCandidateKey")
        target_key = row.payload.get("targetCandidateKey")
        if source_key not in accepted_entity_keys or target_key not in accepted_entity_keys:
            raise ValidationError("已接受的关系引用了未接受的实体候选，请先完成一致复核")

    entity_rows: dict[str, GraphEntity] = {}
    created_entities = reused_entities = created_relations = reused_relations = 0
    try:
        for candidate in entity_candidates:
            candidate_payload = _validate_review_payload(candidate, candidate.payload, snapshot=snapshot)
            existing = ctx.db.scalar(select(GraphEntity).where(
                GraphEntity.workspace_id == ctx.workspace_id,
                GraphEntity.graph_id == graph.id,
                GraphEntity.concept_id == candidate.concept_id,
                GraphEntity.business_key == str(candidate_payload["businessKey"]).strip(),
                GraphEntity.status != "deleted",
            ).with_for_update().execution_options(populate_existing=True))
            if existing:
                if existing.name != candidate_payload["name"] or not _same_properties(
                    concepts[candidate.concept_id], existing.properties, candidate_payload.get("properties", {}),
                ):
                    raise ConflictError("同业务键实体的名称或属性与已确认候选不同，请先处理冲突")
                entity_rows[candidate.candidate_key] = existing
                reused_entities += 1
                continue
            result = graph_service.save_entity(
                ctx,
                graph.id,
                GraphEntityInput(
                    conceptId=candidate.concept_id,
                    businessKey=str(candidate_payload["businessKey"]).strip(),
                    name=str(candidate_payload["name"]).strip(),
                    properties=candidate_payload.get("properties", {}),
                ),
                provenance={
                    "source": "extraction", "jobId": job.id, "runId": run.id,
                    "candidateId": candidate.id, "evidence": candidate.evidence,
                },
                source_candidate_id=candidate.id,
                commit=False,
            )
            entity_rows[candidate.candidate_key] = ctx.db.get(GraphEntity, result["id"])
            created_entities += 1

        for candidate in relation_candidates:
            candidate_payload = _validate_review_payload(candidate, candidate.payload, snapshot=snapshot)
            source = entity_rows[candidate_payload["sourceCandidateKey"]]
            target = entity_rows[candidate_payload["targetCandidateKey"]]
            definition = relations[candidate.relation_type_id]
            expected = (definition.get("sourceConceptId"), definition.get("targetConceptId"))
            actual = (source.concept_id, target.concept_id)
            if actual != expected and not (not definition.get("directed", True) and actual == tuple(reversed(expected))):
                raise ValidationError("实体概念与关系类型的起点、终点约束不一致")
            existing = ctx.db.scalar(select(GraphRelation).where(
                GraphRelation.workspace_id == ctx.workspace_id,
                GraphRelation.graph_id == graph.id,
                GraphRelation.relation_type_id == candidate.relation_type_id,
                GraphRelation.source_entity_id == source.id,
                GraphRelation.target_entity_id == target.id,
                GraphRelation.status != "deleted",
            ).with_for_update().execution_options(populate_existing=True))
            if existing:
                if not _same_properties(definition, existing.properties, candidate_payload.get("properties", {})):
                    raise ConflictError("同端点关系的属性与已确认候选不同，请先处理冲突")
                reused_relations += 1
                continue
            graph_service.save_relation(
                ctx,
                graph.id,
                GraphRelationInput(
                    relationTypeId=candidate.relation_type_id,
                    sourceEntityId=source.id,
                    targetEntityId=target.id,
                    properties=candidate_payload.get("properties", {}),
                ),
                provenance={
                    "source": "extraction", "jobId": job.id, "runId": run.id,
                    "candidateId": candidate.id, "evidence": candidate.evidence,
                },
                source_candidate_id=candidate.id,
                commit=False,
            )
            created_relations += 1

        now = datetime.utcnow()
        result_summary = {
            "graphId": graph.id,
            "runId": run.id,
            "createdEntities": created_entities,
            "reusedEntities": reused_entities,
            "createdRelations": created_relations,
            "reusedRelations": reused_relations,
        }
        graph.updated_at = now
        run.status = "completed"
        run.completed_at = now
        run.summary = {**(run.summary or {}), "materialization": result_summary}
        run.events = [*run.events, {"at": now.isoformat(), "type": "materialized", "message": "候选已写入目标图谱"}]
        # 历史运行可独立物化；任务汇总仅归当前运行，version CAS 防并发覆盖。
        if isinstance(job.result_summary, dict) and job.result_summary.get("runId") == run.id:
            job.status = "completed"
            job.target_graph_id = graph.id
            job.result_summary = {**job.result_summary, **result_summary}
        audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
              action="extraction.materialize", resource_type="knowledge_graph", resource_id=graph.id,
              metadata={**result_summary, "extractionRunId": run.id})
        _commit(ctx, "候选写入图谱时发生业务键或关系冲突")
        return {
            "job": job_view(ctx, job),
            "run": run_view(ctx, run),
            "graph": graph_service.graph_view(ctx, graph, detail=True),
            "result": result_summary,
        }
    except Exception:
        ctx.db.rollback()
        raise
