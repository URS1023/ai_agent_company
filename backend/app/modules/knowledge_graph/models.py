"""Persisted Cloud Brain knowledge-graph and Bot resources with immutable releases."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.models import Base


class Ontology(OwnedRecord, Base):
    """One editable concept model; publication pins a complete immutable snapshot."""
    # 本体（Ontology）：面向业务的概念与关系类型定义，是抽取与图谱的元模型。
    #

    __tablename__ = "kg_ontologies"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_kg_ontology_name"),)
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="本体模型名称")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="业务范围说明")
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, comment="draft/published/archived/deleted")
    published_revision: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="当前正式修订")


class OntologyConcept(OwnedRecord, Base):
    """Concept plus attribute definitions, recognition terms and validation constraints."""
    # 本体概念：一个业务实体类型（如“设备”），attributes 描述其属性 schema。
    #

    __tablename__ = "kg_ontology_concepts"
    __table_args__ = (UniqueConstraint("ontology_id", "code", name="uq_kg_concept_code"),)
    ontology_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_ontologies.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="稳定概念编码")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="概念名称")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    color: Mapped[str] = mapped_column(String(32), default="#3b73d1", nullable=False)
    icon: Mapped[str] = mapped_column(String(80), default="entity", nullable=False)
    attributes: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="字段名、类型、必填、唯一与说明")
    recognition_terms: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="非结构化确定性识别词")
    constraints: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="业务键、最大长度和模式约束")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/deleted")


class OntologyRelationType(OwnedRecord, Base):
    """Typed and constrained edge between two concepts."""
    # 本体关系类型：连接两个概念（source→target），并声明基数约束。
    #

    __tablename__ = "kg_ontology_relation_types"
    __table_args__ = (UniqueConstraint("ontology_id", "code", name="uq_kg_relation_type_code"),)
    ontology_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_ontologies.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="稳定关系编码")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="关系名称")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_concept_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_ontology_concepts.id"), nullable=False, index=True)
    target_concept_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_ontology_concepts.id"), nullable=False, index=True)
    cardinality: Mapped[str] = mapped_column(String(16), default="many_to_many", nullable=False, comment="one_to_one/one_to_many/many_to_one/many_to_many")
    directed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    attributes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)


class OntologyRevision(OwnedRecord, Base):
    """Append-only ontology snapshot used by extraction and published graphs."""
    # 本体正式修订：snapshot 是发布时冻结的定义快照，发布后不可变。
    #

    __tablename__ = "kg_ontology_revisions"
    __table_args__ = (UniqueConstraint("ontology_id", "revision", name="uq_kg_ontology_revision"),)
    ontology_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_ontologies.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), default="发布本体", nullable=False)


class ExtractionJob(OwnedRecord, Base):
    """Reusable structured or unstructured extraction definition."""
    # 抽取任务配置：声明从哪个知识库、以结构化或非结构化方式抽取候选实体与关系。
    #

    __tablename__ = "kg_extraction_jobs"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False, comment="structured/unstructured")
    ontology_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_ontologies.id"), nullable=False, index=True)
    ontology_revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="抽取固定使用的正式本体修订")
    knowledge_base_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("knowledge_bases.id"), nullable=True, index=True)
    document_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    mappings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="实体与关系字段映射")
    input_records: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="结构化输入快照，受接口数量限制")
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, comment="draft/running/review/completed/failed/archived/deleted")
    execution_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    target_graph_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("kg_graphs.id"), nullable=True, index=True)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)


class ExtractionRun(OwnedRecord, Base):
    """One actual extraction attempt with immutable input and ordered event log."""
    # 抽取运行记录：一次任务执行的统计结果（候选数、状态、错误摘要）。
    #

    __tablename__ = "kg_extraction_runs"
    __table_args__ = (UniqueConstraint("job_id", "run_number", name="uq_kg_extraction_run"),)
    job_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_extraction_jobs.id"), nullable=False, index=True)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False, comment="running/review/completed/failed")
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    events: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExtractionCandidate(OwnedRecord, Base):
    """Entity or relation candidate with source evidence and an explicit review decision."""
    # 抽取候选：待人工审核的实体或关系，审核通过后才允许物化进图谱。
    #

    __tablename__ = "kg_extraction_candidates"
    __table_args__ = (UniqueConstraint("run_id", "kind", "candidate_key", name="uq_kg_candidate_key"),)
    job_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_extraction_jobs.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_extraction_runs.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, comment="entity/relation")
    candidate_key: Mapped[str] = mapped_column(String(300), nullable=False, comment="运行内稳定候选键")
    concept_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("kg_ontology_concepts.id"), nullable=True, index=True)
    relation_type_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("kg_ontology_relation_types.id"), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, comment="候选实体或关系数据")
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, comment="文档分块、行号、字段或规则证据")
    review_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, comment="pending/accepted/rejected")
    review_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(26), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class KnowledgeGraph(OwnedRecord, Base):
    """Editable graph instance bound to one ontology."""
    # 知识图谱：本体实例化后的实体与关系集合，可发布为不可变修订。
    #

    __tablename__ = "kg_graphs"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_kg_graph_name"),)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ontology_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_ontologies.id"), nullable=False, index=True)
    ontology_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, comment="draft/published/archived/deleted")
    published_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)


class GraphEntity(OwnedRecord, Base):
    """One typed graph node, unique by concept and business key."""
    # 图谱实体：概念在具体图谱中的一条实例，properties 受概念 schema 约束。
    #

    __tablename__ = "kg_graph_entities"
    __table_args__ = (UniqueConstraint("graph_id", "concept_id", "business_key", name="uq_kg_graph_entity"),)
    graph_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_graphs.id"), nullable=False, index=True)
    concept_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_ontology_concepts.id"), nullable=False, index=True)
    business_key: Mapped[str] = mapped_column(String(300), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    properties: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_candidate_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("kg_extraction_candidates.id"), nullable=True, index=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)


class GraphRelation(OwnedRecord, Base):
    """One typed edge with immutable origin evidence."""
    # 图谱关系：两个实体之间的一条有向关系实例。
    #

    __tablename__ = "kg_graph_relations"
    __table_args__ = (UniqueConstraint("graph_id", "relation_type_id", "source_entity_id", "target_entity_id", name="uq_kg_graph_relation"),)
    graph_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_graphs.id"), nullable=False, index=True)
    relation_type_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_ontology_relation_types.id"), nullable=False, index=True)
    source_entity_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_graph_entities.id"), nullable=False, index=True)
    target_entity_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_graph_entities.id"), nullable=False, index=True)
    properties: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_candidate_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("kg_extraction_candidates.id"), nullable=True, index=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)


class GraphRevision(OwnedRecord, Base):
    """Append-only graph snapshot for publication and downstream Bots."""
    # 图谱正式修订：发布时冻结的实体/关系快照，供 Bot 绑定固定版本。
    #

    __tablename__ = "kg_graph_revisions"
    __table_args__ = (UniqueConstraint("graph_id", "revision", name="uq_kg_graph_revision"),)
    graph_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_graphs.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), default="发布图谱", nullable=False)


class BusinessBot(OwnedRecord, Base):
    """Normal-interface Bot/Agent/Chatflow definition bound to explicit resources."""
    # 业务 Bot：绑定知识库/图谱/工作流/技能/工具的可发布对话实体。
    #

    __tablename__ = "kg_business_bots"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_kg_bot_name"),)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    bot_type: Mapped[str] = mapped_column(String(24), default="agent", nullable=False, comment="agent/knowledge/chatflow")
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    model_profile: Mapped[str] = mapped_column(String(80), default="chat", nullable=False)
    knowledge_base_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    graph_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    workflow_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    skill_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    tool_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    welcome_message: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, comment="draft/published/archived/deleted")
    published_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class BotRevision(OwnedRecord, Base):
    """Immutable Bot configuration used by public invocation and audit replay."""
    # Bot 正式修订：发布时冻结的配置与绑定快照，是外部 API 调用的唯一依据。
    #

    __tablename__ = "kg_bot_revisions"
    __table_args__ = (UniqueConstraint("bot_id", "revision", name="uq_kg_bot_revision"),)
    bot_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_business_bots.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), default="发布 Bot", nullable=False)


class BotDebugRun(OwnedRecord, Base):
    """Persisted Bot debug or API invocation with grounded references."""
    # Bot 运行记录（调试与接口调用共用）：问题、回答、证据链、耗时与错误。
    #

    __tablename__ = "kg_bot_debug_runs"
    bot_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_business_bots.id"), nullable=False, index=True)
    bot_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel: Mapped[str] = mapped_column(String(24), default="debug", nullable=False, comment="debug/api")
    mode: Mapped[str] = mapped_column(String(24), default="retrieval", nullable=False, comment="retrieval/model")
    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    references: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False, comment="running/completed/failed")
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class BotApiKey(OwnedRecord, Base):
    """Hashed Bot API credential; plaintext is returned exactly once."""
    # Bot API 密钥：只存 sha256 摘要与前缀，明文仅在创建时返回一次。
    #

    __tablename__ = "kg_bot_api_keys"
    bot_id: Mapped[str] = mapped_column(String(26), ForeignKey("kg_business_bots.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    key_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/revoked")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
