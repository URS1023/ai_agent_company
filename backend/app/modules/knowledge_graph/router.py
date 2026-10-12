"""Documented APIs for Cloud Brain ontology, extraction, graph and Bot modules."""
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.common.contracts import Envelope, MutationResult
from app.common.deps import AuthContext, get_current_context, get_db
from app.modules.knowledge_graph import bots, extraction, graphs, ontology
from app.modules.knowledge_graph.schemas import (
    BotApiKeyInput,
    BotDebugInput,
    BotInput,
    CandidateBatchReview,
    ConceptInput,
    ExtractionExecuteInput,
    ExtractionJobInput,
    ExtractionMaterializeInput,
    GraphEntityInput,
    GraphInput,
    GraphRelationInput,
    OntologyInput,
    PublicBotInvokeInput,
    PublishInput,
    RelationTypeInput,
)


router = APIRouter()
ontology_router = APIRouter(prefix="/api/v1/ontologies", tags=["概念与关系模型"])
extraction_router = APIRouter(prefix="/api/v1/extractions", tags=["知识抽取与复核"])
graph_router = APIRouter(prefix="/api/v1/graphs", tags=["知识图谱"])
bot_router = APIRouter(prefix="/api/v1/bots", tags=["Bot 与智能体"])
public_bot_router = APIRouter(prefix="/api/public/v1/bots", tags=["Bot 公开调用"])

Context = Annotated[AuthContext, Depends(get_current_context)]
Database = Annotated[Session, Depends(get_db)]
Version = Annotated[int, Query(ge=1, description="界面读取到的乐观锁版本")]
Keyword = Annotated[str, Query(max_length=200, description="名称关键词")]
Limit = Annotated[int, Query(ge=1, le=500, description="每页条数")]
Offset = Annotated[int, Query(ge=0, le=100000, description="分页偏移")]


@ontology_router.get("", response_model=Envelope[list[dict]], operation_id="ontologies_list", summary="查询本体模型")
def ontology_list(ctx: Context, keyword: Keyword = ""):
    """需要 ontology.view；仅返回当前工作区及动作数据范围内的概念模型。"""
    # GET /api/v1/ontologies —— 查询本体模型列表（keyword 匹配名称）。只读，登录即可。
    #
    return Envelope(data=ontology.list_ontologies(ctx, keyword))


@ontology_router.post("", status_code=201, response_model=Envelope[dict], operation_id="ontologies_create", summary="创建本体模型")
def ontology_create(payload: OntologyInput, ctx: Context):
    """需要 ontology.create；只建立草稿，不自动创建概念或发布版本。"""
    # POST /api/v1/ontologies —— 新建本体，返回 MutationResult。异常：422 参数非法。
    #
    return Envelope(data=ontology.save_ontology(ctx, payload))


@ontology_router.get("/{ontology_id}", response_model=Envelope[dict], operation_id="ontologies_get", summary="读取本体与可视化关系")
def ontology_get(ontology_id: str, ctx: Context):
    """需要 ontology.view；返回概念、属性、识别词、关系约束与编辑版本。"""
    # GET /api/v1/ontologies/{ontology_id} —— 本体详情（含概念与关系类型明细）。异常：404。
    #
    row = ontology.get_ontology(ctx, ontology_id)
    return Envelope(data=ontology.ontology_view(ctx, row, detail=True))


@ontology_router.put("/{ontology_id}", response_model=Envelope[dict], operation_id="ontologies_update", summary="修改本体基本信息")
def ontology_update(ontology_id: str, payload: OntologyInput, ctx: Context):
    """需要 ontology.edit 与 version；不改写既有正式修订。"""
    # PUT /api/v1/ontologies/{ontology_id} —— 更新本体（乐观锁 version 必填）。异常：404 / 409。
    #
    return Envelope(data=ontology.save_ontology(ctx, payload, ontology_id))


@ontology_router.delete("/{ontology_id}", response_model=Envelope[MutationResult], operation_id="ontologies_delete", summary="移除未被引用的本体")
def ontology_delete(ontology_id: str, ctx: Context, version: Version):
    """需要 ontology.delete；抽取任务或图谱仍引用时返回 409，不级联删除业务数据。"""
    # DELETE /api/v1/ontologies/{ontology_id} —— 软删本体（version 走查询参数）。异常：404 / 409 被引用。
    #
    ontology.remove_ontology(ctx, ontology_id, version)
    return Envelope(data=MutationResult(id=ontology_id, deleted=True))


@ontology_router.post("/{ontology_id}/concepts", status_code=201, response_model=Envelope[dict], operation_id="ontology_concepts_create", summary="新增概念及属性")
def concept_create(ontology_id: str, payload: ConceptInput, ctx: Context):
    """需要 ontology.create；属性字段、类型、必填项、业务键和非结构化识别词均严格校验。"""
    # POST /api/v1/ontologies/{ontology_id}/concepts —— 新建概念。异常：404 本体不存在、422 定义非法。
    #
    return Envelope(data=ontology.save_concept(ctx, ontology_id, payload))


@ontology_router.put("/{ontology_id}/concepts/{concept_id}", response_model=Envelope[dict], operation_id="ontology_concepts_update", summary="修改概念草稿")
def concept_update(ontology_id: str, concept_id: str, payload: ConceptInput, ctx: Context):
    """需要 ontology.edit 与 version；不会修改已发布修订中的概念快照。"""
    # PUT /api/v1/ontologies/{ontology_id}/concepts/{concept_id} —— 更新概念。异常：404 / 409。
    #
    return Envelope(data=ontology.save_concept(ctx, ontology_id, payload, concept_id))


@ontology_router.delete("/{ontology_id}/concepts/{concept_id}", response_model=Envelope[MutationResult], operation_id="ontology_concepts_delete", summary="移除未使用概念")
def concept_delete(ontology_id: str, concept_id: str, ctx: Context, version: Version):
    """需要 ontology.delete；被关系类型或图谱实体引用时拒绝删除。"""
    # DELETE /api/v1/ontologies/{ontology_id}/concepts/{concept_id} —— 删除概念；被关系引用时 409。
    #
    ontology.remove_concept(ctx, ontology_id, concept_id, version)
    return Envelope(data=MutationResult(id=concept_id, deleted=True))


@ontology_router.post("/{ontology_id}/relation-types", status_code=201, response_model=Envelope[dict], operation_id="ontology_relation_types_create", summary="新增关系类型")
def relation_type_create(ontology_id: str, payload: RelationTypeInput, ctx: Context):
    """需要 ontology.create；验证起点、终点概念、方向、基数和关系属性。"""
    # POST /api/v1/ontologies/{ontology_id}/relation-types —— 新建关系类型。异常：404 / 422。
    #
    return Envelope(data=ontology.save_relation(ctx, ontology_id, payload))


@ontology_router.put("/{ontology_id}/relation-types/{relation_id}", response_model=Envelope[dict], operation_id="ontology_relation_types_update", summary="修改关系类型草稿")
def relation_type_update(ontology_id: str, relation_id: str, payload: RelationTypeInput, ctx: Context):
    """需要 ontology.edit 与 version；正式修订保持不可变。"""
    # PUT /api/v1/ontologies/{ontology_id}/relation-types/{relation_id} —— 更新关系类型。异常：404 / 409。
    #
    return Envelope(data=ontology.save_relation(ctx, ontology_id, payload, relation_id))


@ontology_router.delete("/{ontology_id}/relation-types/{relation_id}", response_model=Envelope[MutationResult], operation_id="ontology_relation_types_delete", summary="移除未使用关系类型")
def relation_type_delete(ontology_id: str, relation_id: str, ctx: Context, version: Version):
    """需要 ontology.delete；图谱关系仍引用时拒绝删除。"""
    # DELETE /api/v1/ontologies/{ontology_id}/relation-types/{relation_id} —— 删除关系类型；被实例引用时 409。
    #
    ontology.remove_relation(ctx, ontology_id, relation_id, version)
    return Envelope(data=MutationResult(id=relation_id, deleted=True))


@ontology_router.post("/{ontology_id}/publish", response_model=Envelope[dict], operation_id="ontologies_publish", summary="发布不可变本体修订")
def ontology_publish(ontology_id: str, payload: PublishInput, ctx: Context):
    """需要 ontology.publish；固化概念、属性、识别词和关系约束，供抽取与图谱明确引用。"""
    # POST /api/v1/ontologies/{ontology_id}/publish —— 发布本体正式修订，冻结定义快照。异常：404 / 409。
    #
    return Envelope(data=ontology.publish_ontology(ctx, ontology_id, payload))


@ontology_router.get("/{ontology_id}/revisions", response_model=Envelope[list[dict]], operation_id="ontologies_revisions_list", summary="查看本体正式修订")
def ontology_revision_list(ontology_id: str, ctx: Context):
    """需要 ontology.view；历史快照只读，后续编辑不会回写。"""
    # GET /api/v1/ontologies/{ontology_id}/revisions —— 查询本体正式修订历史。异常：404。
    #
    return Envelope(data=ontology.ontology_revisions(ctx, ontology_id))


@extraction_router.get("", response_model=Envelope[list[dict]], operation_id="extractions_list", summary="查询知识抽取任务")
def extraction_list(ctx: Context, keyword: Keyword = "", mode: str = "", status: str = ""):
    """需要 extraction.view；区分结构化记录和知识文档抽取。"""
    # GET /api/v1/extractions —— 查询抽取任务（keyword/mode/status 筛选）。只读。
    #
    return Envelope(data=extraction.list_jobs(ctx, keyword=keyword, mode=mode, status=status))


@extraction_router.post("", status_code=201, response_model=Envelope[dict], operation_id="extractions_create", summary="创建抽取任务")
def extraction_create(payload: ExtractionJobInput, ctx: Context):
    """需要 extraction.create、ontology.view，文档模式另需 knowledge.view；固定使用当前正式本体修订。"""
    # POST /api/v1/extractions —— 新建抽取任务。异常：404 知识库不可见、422 配置非法。
    #
    return Envelope(data=extraction.save_job(ctx, payload))


@extraction_router.get("/{job_id}", response_model=Envelope[dict], operation_id="extractions_get", summary="读取抽取配置和运行历史")
def extraction_get(job_id: str, ctx: Context):
    """需要 extraction.view；返回真实来源配置、字段映射、运行及复核摘要。"""
    # GET /api/v1/extractions/{job_id} —— 抽取任务详情。异常：404。
    #
    return Envelope(data=extraction.job_view(ctx, extraction.get_job(ctx, job_id), detail=True))


@extraction_router.put("/{job_id}", response_model=Envelope[dict], operation_id="extractions_update", summary="修改抽取任务")
def extraction_update(job_id: str, payload: ExtractionJobInput, ctx: Context):
    """需要 extraction.edit 与 version；正在执行的任务不能修改，旧运行快照保留。"""
    # PUT /api/v1/extractions/{job_id} —— 更新抽取任务（乐观锁）。异常：404 / 409。
    #
    return Envelope(data=extraction.save_job(ctx, payload, job_id))


@extraction_router.delete("/{job_id}", response_model=Envelope[MutationResult], operation_id="extractions_delete", summary="移除抽取任务")
def extraction_delete(job_id: str, ctx: Context, version: Version):
    """需要 extraction.delete；软删除任务，保留候选、证据和已写入图谱的来源。"""
    # DELETE /api/v1/extractions/{job_id} —— 软删抽取任务。异常：404 / 409。
    #
    extraction.remove_job(ctx, job_id, version)
    return Envelope(data=MutationResult(id=job_id, deleted=True))


@extraction_router.post("/{job_id}/execute", response_model=Envelope[dict], operation_id="extractions_execute", summary="执行抽取并生成待复核候选")
def extraction_execute(job_id: str, payload: ExtractionExecuteInput, ctx: Context):
    """需要 extraction.execute；结构化读取输入快照，非结构化读取真实知识分块，不用演示数据补空结果。"""
    # POST /api/v1/extractions/{job_id}/execute —— 执行一次抽取，生成待审核候选。异常：404 / 422。
    #
    return Envelope(data=extraction.execute_job(ctx, job_id, payload))


@extraction_router.get("/{job_id}/runs", response_model=Envelope[list[dict]], operation_id="extractions_runs_list", summary="查看抽取运行与事件")
def extraction_runs(job_id: str, ctx: Context):
    """需要 extraction.view；失败运行保留错误摘要，读取不会重新执行。"""
    # GET /api/v1/extractions/{job_id}/runs —— 查询该任务的历史运行记录。异常：404。
    #
    return Envelope(data=extraction.list_runs(ctx, job_id))


@extraction_router.get("/{job_id}/runs/{run_id}/candidates", response_model=Envelope[list[dict]], operation_id="extractions_candidates_list", summary="分页查看候选与来源证据")
def extraction_candidates(job_id: str, run_id: str, ctx: Context, kind: str = "", reviewStatus: str = "",
                          limit: Limit = 200, offset: Offset = 0):
    """需要 extraction.view；每个候选返回记录字段或文档分块、位置、原文片段等可追溯证据。"""
    # GET /api/v1/extractions/{job_id}/runs/{run_id}/candidates —— 分页查询候选（kind/reviewStatus 筛选）。
    #
    return Envelope(data=extraction.list_candidates(
        ctx, job_id, run_id, kind=kind, review_status=reviewStatus, limit=limit, offset=offset,
    ))


@extraction_router.post("/{job_id}/runs/{run_id}/review", response_model=Envelope[dict], operation_id="extractions_candidates_review", summary="批量接受、驳回或修正候选")
def extraction_review(job_id: str, run_id: str, payload: CandidateBatchReview, ctx: Context):
    """需要 extraction.edit；逐项校验 version，复核可修正候选内容但不能改变其概念或关系类型。"""
    # POST /api/v1/extractions/{job_id}/runs/{run_id}/review —— 批量审核候选（通过/驳回）。异常：404 / 422。
    #
    return Envelope(data=extraction.review_candidates(ctx, job_id, run_id, payload))


@extraction_router.post("/{job_id}/materialize", response_model=Envelope[dict], operation_id="extractions_materialize", summary="将已接受候选写入目标图谱")
def extraction_materialize(job_id: str, payload: ExtractionMaterializeInput, ctx: Context):
    """需要 extraction.execute、graphs.edit/create；本体修订必须一致，实体与关系在同一事务中写入。"""
    # POST /api/v1/extractions/{job_id}/materialize —— 把审核通过的候选物化进目标图谱。异常：404 / 409。
    #
    return Envelope(data=extraction.materialize(ctx, job_id, payload))


@graph_router.get("", response_model=Envelope[list[dict]], operation_id="graphs_list", summary="查询知识图谱")
def graph_list(ctx: Context, keyword: Keyword = ""):
    """需要 graphs.view；返回固定本体修订、实体关系数量及发布状态。"""
    # GET /api/v1/graphs —— 查询图谱列表（keyword 匹配名称）。只读。
    #
    return Envelope(data=graphs.list_graphs(ctx, keyword))


@graph_router.post("", status_code=201, response_model=Envelope[dict], operation_id="graphs_create", summary="创建知识图谱")
def graph_create(payload: GraphInput, ctx: Context):
    """需要 graphs.create 和 ontology.view；选择本体后固定其当前正式修订，不自动套默认本体。"""
    # POST /api/v1/graphs —— 新建图谱（须绑定已发布本体）。异常：404 / 422。
    #
    return Envelope(data=graphs.save_graph(ctx, payload))


@graph_router.get("/{graph_id}", response_model=Envelope[dict], operation_id="graphs_get", summary="读取图谱与统计")
def graph_get(graph_id: str, ctx: Context):
    """需要 graphs.view；返回实体、关系、类型和孤立节点统计。"""
    # GET /api/v1/graphs/{graph_id} —— 图谱详情。异常：404。
    #
    return Envelope(data=graphs.graph_view(ctx, graphs.get_graph(ctx, graph_id), detail=True))


@graph_router.put("/{graph_id}", response_model=Envelope[dict], operation_id="graphs_update", summary="修改图谱基本信息")
def graph_update(graph_id: str, payload: GraphInput, ctx: Context):
    """需要 graphs.edit 与 version；有实体或关系后不能更换本体。"""
    # PUT /api/v1/graphs/{graph_id} —— 更新图谱（乐观锁）。异常：404 / 409。
    #
    return Envelope(data=graphs.save_graph(ctx, payload, graph_id))


@graph_router.delete("/{graph_id}", response_model=Envelope[MutationResult], operation_id="graphs_delete", summary="移除未被 Bot 使用的图谱")
def graph_delete(graph_id: str, ctx: Context, version: Version):
    """需要 graphs.delete；软删除，Bot 仍绑定时返回 409。"""
    # DELETE /api/v1/graphs/{graph_id} —— 归档/软删图谱。异常：404 / 409。
    #
    graphs.remove_graph(ctx, graph_id, version)
    return Envelope(data=MutationResult(id=graph_id, deleted=True))


@graph_router.get("/{graph_id}/entities", response_model=Envelope[list[dict]], operation_id="graph_entities_list", summary="分页查询图谱实体")
def graph_entities(graph_id: str, ctx: Context, keyword: Keyword = "", conceptId: str = "",
                   limit: Limit = 100, offset: Offset = 0):
    """需要 graphs.view；可按概念、名称和稳定业务键筛选。"""
    # GET /api/v1/graphs/{graph_id}/entities —— 分页查询实体（keyword/conceptId 筛选）。
    #
    return Envelope(data=graphs.list_entities(
        ctx, graph_id, keyword=keyword, concept_id=conceptId, limit=limit, offset=offset,
    ))


@graph_router.post("/{graph_id}/entities", status_code=201, response_model=Envelope[dict], operation_id="graph_entities_create", summary="手工新增图谱实体")
def graph_entity_create(graph_id: str, payload: GraphEntityInput, ctx: Context):
    """需要 graphs.create；属性必须符合图谱固定本体修订，来源标记为人工。"""
    # POST /api/v1/graphs/{graph_id}/entities —— 新建实体（属性按概念 schema 校验）。异常：422。
    #
    return Envelope(data=graphs.save_entity(ctx, graph_id, payload))


@graph_router.put("/{graph_id}/entities/{entity_id}", response_model=Envelope[dict], operation_id="graph_entities_update", summary="修改图谱实体")
def graph_entity_update(graph_id: str, entity_id: str, payload: GraphEntityInput, ctx: Context):
    """需要 graphs.edit 与 version；不能修改已有实体的概念类型。"""
    # PUT /api/v1/graphs/{graph_id}/entities/{entity_id} —— 更新实体。异常：404 / 409。
    #
    return Envelope(data=graphs.save_entity(ctx, graph_id, payload, entity_id))


@graph_router.delete("/{graph_id}/entities/{entity_id}", response_model=Envelope[MutationResult], operation_id="graph_entities_delete", summary="移除无关系实体")
def graph_entity_delete(graph_id: str, entity_id: str, ctx: Context, version: Version):
    """需要 graphs.delete；实体仍有关系时返回 409，防止悬空边。"""
    # DELETE /api/v1/graphs/{graph_id}/entities/{entity_id} —— 软删实体，并连带软删相连关系。异常：404 / 409。
    #
    graphs.remove_entity(ctx, graph_id, entity_id, version)
    return Envelope(data=MutationResult(id=entity_id, deleted=True))


@graph_router.get("/{graph_id}/relations", response_model=Envelope[list[dict]], operation_id="graph_relations_list", summary="分页查询图谱关系")
def graph_relations(graph_id: str, ctx: Context, limit: Limit = 100, offset: Offset = 0):
    """需要 graphs.view；返回关系类型、端点、属性和来源证据。"""
    # GET /api/v1/graphs/{graph_id}/relations —— 按 limit/offset 查询关系。异常：404。
    #
    return Envelope(data=graphs.list_relations(ctx, graph_id, limit=limit, offset=offset))


@graph_router.post("/{graph_id}/relations", status_code=201, response_model=Envelope[dict], operation_id="graph_relations_create", summary="手工新增图谱关系")
def graph_relation_create(graph_id: str, payload: GraphRelationInput, ctx: Context):
    """需要 graphs.create；校验起止概念、方向、基数和属性定义。"""
    # POST /api/v1/graphs/{graph_id}/relations —— 新建关系（校验两端实体与基数约束）。异常：422。
    #
    return Envelope(data=graphs.save_relation(ctx, graph_id, payload))


@graph_router.put("/{graph_id}/relations/{relation_id}", response_model=Envelope[dict], operation_id="graph_relations_update", summary="修改图谱关系")
def graph_relation_update(graph_id: str, relation_id: str, payload: GraphRelationInput, ctx: Context):
    """需要 graphs.edit 与 version；违反一对一、一对多或多对一约束时返回 409。"""
    # PUT /api/v1/graphs/{graph_id}/relations/{relation_id} —— 更新关系。异常：404 / 409。
    #
    return Envelope(data=graphs.save_relation(ctx, graph_id, payload, relation_id))


@graph_router.delete("/{graph_id}/relations/{relation_id}", response_model=Envelope[MutationResult], operation_id="graph_relations_delete", summary="移除图谱关系")
def graph_relation_delete(graph_id: str, relation_id: str, ctx: Context, version: Version):
    """需要 graphs.delete；软删除关系并保留正式修订中的历史快照。"""
    # DELETE /api/v1/graphs/{graph_id}/relations/{relation_id} —— 软删关系。异常：404 / 409。
    #
    graphs.remove_relation(ctx, graph_id, relation_id, version)
    return Envelope(data=MutationResult(id=relation_id, deleted=True))


@graph_router.get("/{graph_id}/explore", response_model=Envelope[dict], operation_id="graphs_explore", summary="按实体或关键词探索关联网络")
def graph_explore(graph_id: str, ctx: Context, query: str = Query("", max_length=300), entityId: str = "",
                  depth: int = Query(1, ge=0, le=4), limit: int = Query(200, ge=1, le=500)):
    """需要 graphs.view；在当前图谱内按最多四层扩展，返回稳定节点和边，不跨工作区。"""
    # GET /api/v1/graphs/{graph_id}/explore —— 图谱探索：按关键字或指定实体展开其邻居，供可视化定位。
    #
    return Envelope(data=graphs.explore(ctx, graph_id, query=query, entity_id=entityId, depth=depth, limit=limit))


@graph_router.get("/{graph_id}/statistics", response_model=Envelope[dict], operation_id="graphs_statistics", summary="查看图谱质量统计")
def graph_statistics(graph_id: str, ctx: Context):
    """需要 graphs.view；统计概念、关系类型、实体、关系和孤立实体。"""
    # GET /api/v1/graphs/{graph_id}/statistics —— 图谱统计（实体数/关系数/概念分布）。异常：404。
    #
    return Envelope(data=graphs.graph_statistics(ctx, graph_id))


@graph_router.post("/{graph_id}/publish", response_model=Envelope[dict], operation_id="graphs_publish", summary="发布不可变图谱修订")
def graph_publish(graph_id: str, payload: PublishInput, ctx: Context):
    """需要 graphs.publish；固化实体、关系、来源和统计，Bot 正式版只绑定已发布图谱。"""
    # POST /api/v1/graphs/{graph_id}/publish —— 发布图谱正式修订，冻结实体/关系快照。异常：404 / 409。
    #
    return Envelope(data=graphs.publish_graph(ctx, graph_id, payload))


@graph_router.get("/{graph_id}/revisions", response_model=Envelope[list[dict]], operation_id="graphs_revisions_list", summary="查看图谱正式修订")
def graph_revisions(graph_id: str, ctx: Context):
    """需要 graphs.view；正式快照只读并保留原始抽取来源。"""
    # GET /api/v1/graphs/{graph_id}/revisions —— 查询图谱正式修订历史。异常：404。
    #
    return Envelope(data=graphs.graph_revisions(ctx, graph_id))


@bot_router.get("", response_model=Envelope[list[dict]], operation_id="bots_list", summary="查询 Bot 与智能体")
def bot_list(ctx: Context, keyword: Keyword = "", botType: str = "", status: str = ""):
    """需要 bots.view；返回类型、资源绑定数量、发布和 API 状态。"""
    # GET /api/v1/bots —— 查询 Bot 列表（keyword/botType/status 筛选）。只读。
    #
    return Envelope(data=bots.list_bots(ctx, keyword=keyword, bot_type=botType, status=status))


@bot_router.post("", status_code=201, response_model=Envelope[dict], operation_id="bots_create", summary="创建业务 Bot 草稿")
def bot_create(payload: BotInput, ctx: Context):
    """需要 bots.create 及全部绑定资源的查看权限；AI 工作台不是 Bot 管理的替代界面。"""
    # POST /api/v1/bots —— 新建 Bot 草稿（绑定逐项做权限校验）。异常：404 / 422。
    #
    return Envelope(data=bots.save_bot(ctx, payload))


@bot_router.get("/binding-options", response_model=Envelope[dict], operation_id="bots_binding_options", summary="查询当前可绑定业务资源")
def bot_binding_options(ctx: Context):
    """需要 bots.view；按各资源查看权限和数据范围返回知识库、图谱、流程、Skill 与工具选项。"""
    # GET /api/v1/bots/binding-options —— 返回当前账号可绑定的知识库/图谱/工作流/Skill/工具候选。
    # 权限不足的类型返回空数组而不是报错。
    #
    return Envelope(data=bots.binding_options(ctx))


@bot_router.get("/{bot_id}", response_model=Envelope[dict], operation_id="bots_get", summary="读取 Bot 配置")
def bot_get(bot_id: str, ctx: Context):
    """需要 bots.view；返回草稿配置，不返回任何 API Key 摘要。"""
    # GET /api/v1/bots/{bot_id} —— Bot 详情（含绑定 id 列表）。异常：404。
    #
    return Envelope(data=bots.bot_view(bots.get_bot(ctx, bot_id), detail=True))


@bot_router.put("/{bot_id}", response_model=Envelope[dict], operation_id="bots_update", summary="修改 Bot 草稿和资源绑定")
def bot_update(bot_id: str, payload: BotInput, ctx: Context):
    """需要 bots.edit 与 version；修改草稿不会改变公开调用使用的正式修订。"""
    # PUT /api/v1/bots/{bot_id} —— 更新 Bot 草稿（乐观锁）。异常：404 / 409。
    #
    return Envelope(data=bots.save_bot(ctx, payload, bot_id))


@bot_router.delete("/{bot_id}", response_model=Envelope[MutationResult], operation_id="bots_delete", summary="移除 Bot 并吊销 API Key")
def bot_delete(bot_id: str, ctx: Context, version: Version):
    """需要 bots.delete；软删除 Bot，立即吊销其有效 Key，保留调试与调用记录。"""
    # DELETE /api/v1/bots/{bot_id} —— 软删 Bot，并连带撤销其全部 API Key。异常：404 / 409。
    #
    bots.remove_bot(ctx, bot_id, version)
    return Envelope(data=MutationResult(id=bot_id, deleted=True))


@bot_router.post("/{bot_id}/debug", response_model=Envelope[dict], operation_id="bots_debug", summary="使用真实绑定资源调试 Bot")
def bot_debug(bot_id: str, payload: BotDebugInput, ctx: Context):
    """需要 bots.execute；检索模式确定性返回证据，模型模式无证据时拒绝生成无依据回答。"""
    # POST /api/v1/bots/{bot_id}/debug —— 调试运行 Bot（可指定跑正式版或当前草稿）。
    # 副作用：真实执行绑定的工作流/工具并调用模型；异常：404 / 422。
    #
    return Envelope(data=bots.debug_bot(ctx, bot_id, payload))


@bot_router.get("/{bot_id}/runs", response_model=Envelope[list[dict]], operation_id="bots_runs_list", summary="查看 Bot 调试与 API 调用记录")
def bot_runs(bot_id: str, ctx: Context, limit: int = Query(100, ge=1, le=500)):
    """需要 bots.view；返回问题、回答、来源、状态和耗时，不返回调用凭据。"""
    # GET /api/v1/bots/{bot_id}/runs —— 查询该 Bot 的运行记录（含证据链）。异常：404。
    #
    return Envelope(data=bots.list_debug_runs(ctx, bot_id, limit=limit))


@bot_router.post("/{bot_id}/publish", response_model=Envelope[dict], operation_id="bots_publish", summary="发布固定资源版本的 Bot")
def bot_publish(bot_id: str, payload: PublishInput, ctx: Context):
    """需要 bots.publish；图谱、流程、Skill 和工具必须已有正式版本，快照不会随草稿漂移。"""
    # POST /api/v1/bots/{bot_id}/publish —— 发布 Bot 正式修订（要求绑定资源已发布）。异常：404 / 409。
    #
    return Envelope(data=bots.publish_bot(ctx, bot_id, payload))


@bot_router.get("/{bot_id}/revisions", response_model=Envelope[list[dict]], operation_id="bots_revisions_list", summary="查看 Bot 正式修订")
def bot_revisions(bot_id: str, ctx: Context):
    """需要 bots.view；返回固定配置和资源版本，不包含密钥。"""
    # GET /api/v1/bots/{bot_id}/revisions —— 查询 Bot 正式修订历史。异常：404。
    #
    return Envelope(data=bots.list_revisions(ctx, bot_id))


@bot_router.post("/{bot_id}/api-keys", status_code=201, response_model=Envelope[dict], operation_id="bots_api_keys_create", summary="创建只显示一次的 Bot API Key")
def bot_api_key_create(bot_id: str, payload: BotApiKeyInput, ctx: Context):
    """需要 bots.publish；Bot 必须已发布并启用 API，服务端仅保存摘要，明文只在本次响应出现。"""
    # POST /api/v1/bots/{bot_id}/api-keys —— 创建 API Key，明文仅在本次响应返回一次。异常：404 / 422 未发布。
    #
    return Envelope(data=bots.create_api_key(ctx, bot_id, payload))


@bot_router.get("/{bot_id}/api-keys", response_model=Envelope[list[dict]], operation_id="bots_api_keys_list", summary="查看 Bot API Key 状态")
def bot_api_keys(bot_id: str, ctx: Context):
    """需要 bots.view；仅返回名称、前缀、状态、到期和最后使用时间。"""
    # GET /api/v1/bots/{bot_id}/api-keys —— 查询 API Key（只返回前缀，不返回密钥本体）。异常：404。
    #
    return Envelope(data=bots.list_api_keys(ctx, bot_id))


@bot_router.delete("/{bot_id}/api-keys/{key_id}", response_model=Envelope[dict], operation_id="bots_api_keys_revoke", summary="吊销 Bot API Key")
def bot_api_key_revoke(bot_id: str, key_id: str, ctx: Context, version: Version):
    """需要 bots.publish 与 version；吊销后不可恢复，历史调用记录保留。"""
    # DELETE /api/v1/bots/{bot_id}/api-keys/{key_id} —— 撤销 API Key（乐观锁 version）。异常：404 / 409。
    #
    return Envelope(data=bots.revoke_api_key(ctx, bot_id, key_id, version))


@public_bot_router.post("/{bot_id}/invoke", response_model=Envelope[dict], operation_id="public_bots_invoke", summary="使用 API Key 调用已发布 Bot")
def public_bot_invoke(bot_id: str, payload: PublicBotInvokeInput, db: Database,
                      api_key: Annotated[str, Header(alias="X-Bot-Key", min_length=24, max_length=200)]):
    """无需用户会话；Key 只匹配指定 Bot，始终使用当前正式快照并持久化调用、证据和耗时。"""
    # POST /api/public/v1/bots/{bot_id}/invoke —— 无需登录的公开调用入口，用 X-Bot-Key 头携带 API Key。
    # 鉴权失败一律返回同一句错误文案，避免通过文案区分 bot/key 状态；
    # 以 Bot 所有者身份执行，因此其角色/授权变更会实时生效。异常：404（统一文案）、422、运行期错误。
    #
    return Envelope(data=bots.invoke_public(
        db, bot_id=bot_id, api_key=api_key, query=payload.query, mode=payload.mode,
    ))


for child in (ontology_router, extraction_router, graph_router, bot_router, public_bot_router):
    router.include_router(child)
