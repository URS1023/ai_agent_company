"""Documented API collection for the Cloud Brain tool and connector capability center.

中文补充（工具与能力中心 HTTP 层）：
- 路由前缀 ``/api/v1/capabilities``，覆盖三类资源：工具分类（categories）、
  工具方法与参数（tools/methods）、连接器及其版本（connectors）。
- 鉴权：所有端点都依赖 get_current_context；服务层再按权限码与角色数据范围
  （connectors.view / connectors.execute、数据源范围等）过滤，不在此层做业务判断。
- 写操作遵循"版本化修改"：分类/方法带 version 参数做乐观锁，连接器修改一律
  产生新草稿版本，已发布版本不可覆盖（发布后工具网关只解析该版本）。
- 响应统一用 Envelope 包装；分类/方法导出返回带 BOM 的 CSV（Excel 直接打开不乱码）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.common.contracts import Envelope, MutationResult
from app.common.deps import AuthContext, get_current_context
from app.modules.capabilities import catalog_service, service
from app.modules.capabilities.schemas import (
    ConnectorCloneInput,
    ConnectorInput,
    ConnectorShareInput,
    ConnectorTestInput,
    ConnectorVersionInput,
    FromRunInput,
    PublishInput,
    ToolCategoryInput,
    ToolCategoryPatch,
    ToolArgumentsInput,
    ToolCompileInput,
    ToolMethodBatchInput,
    ToolMethodPatch,
)

router = APIRouter(prefix="/api/v1/capabilities", tags=["工具与能力管理"])
Context = Annotated[AuthContext, Depends(get_current_context)]


@router.get("/tools", response_model=Envelope[dict], operation_id="capability_tools_list", summary="查询当前账号可用工具")
def tools_list(
    ctx: Context,
    keyword: str = Query("", max_length=200),
    provider: str = Query("", max_length=30),
    category_id: str = Query("", max_length=26),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0, le=100000),
):
    """内置工具按实时权限过滤；连接器工具还应用工作区和角色数据范围。"""
    return Envelope(data=service.list_tools(
        ctx, keyword=keyword, provider=provider, category_id=category_id,
        limit=limit, offset=offset,
    ))


@router.get("/categories", response_model=Envelope[dict], operation_id="capability_categories_list", summary="查看工具分类树")
def categories_list(ctx: Context):
    """分类只组织真实已登记工具，不创建无法执行的占位工具。"""
    return Envelope(data=catalog_service.list_categories(ctx))


@router.post("/categories", response_model=Envelope[dict], status_code=201, operation_id="capability_categories_create", summary="新增工具分类")
def categories_create(payload: ToolCategoryInput, ctx: Context):
    """POST /api/v1/capabilities/categories：新建工具分类，返回 201。

    需要分类维护权限（由 catalog_service 校验）；同一工作区内同级不能重名，
    name/code 冲突返回校验错误。分类只是组织手段，不会创建任何可执行工具。
    """
    return Envelope(data=catalog_service.create_category(ctx, payload))


@router.patch("/categories/{category_id}", response_model=Envelope[dict], operation_id="capability_categories_update", summary="版本化修改工具分类")
def categories_update(category_id: str, payload: ToolCategoryPatch, ctx: Context):
    """PATCH /api/v1/capabilities/categories/{category_id}：按 version 乐观锁修改分类。

    携带的 version 与当前记录不一致时返回冲突错误，避免多人同时改动互相覆盖；
    调整父节点会校验层级合法性（见 catalog_service._validate_parent）。
    """
    return Envelope(data=catalog_service.update_category(ctx, category_id, payload))


@router.delete("/categories/{category_id}", response_model=Envelope[MutationResult], operation_id="capability_categories_archive", summary="归档空工具分类")
def categories_archive(category_id: str, ctx: Context, version: int = Query(ge=1)):
    """DELETE /api/v1/capabilities/categories/{category_id}?version=：归档分类（软删除）。

    必须传 version 做乐观锁；仅当分类下没有子分类与已归类工具时才允许归档，
    避免把仍在使用的工具变成"无分类"的悬挂状态。
    """
    return Envelope(data=MutationResult(**catalog_service.archive_category(ctx, category_id, version)))


@router.get("/categories-export", operation_id="capability_categories_export", summary="导出工具分类 CSV")
def categories_export(ctx: Context):
    """GET /api/v1/capabilities/categories-export：导出分类树为 CSV。

    返回 text/csv 附件（前置 BOM 便于 Excel 正确识别 UTF-8 中文）；
    导出内容不受分页参数影响，但仍按当前账号可见范围生成。
    """
    return Response(
        content="\ufeff" + catalog_service.categories_csv(ctx),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tool-categories.csv"'},
    )


@router.get("/methods", response_model=Envelope[dict], operation_id="capability_methods_list", summary="查看工具方法及治理元数据")
def methods_list(
    ctx: Context,
    keyword: str = Query("", max_length=200),
    provider: str = Query("", max_length=30),
    category_id: str = Query("", max_length=26),
    enabled: bool | None = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
):
    """GET /api/v1/capabilities/methods：分页查看工具方法及其治理元数据。

    参数：keyword/provider/category_id 为筛选条件；``enabled=None`` 表示不过滤启停
    状态（传 true/false 可只取启用或停用的方法）。limit 上限 200，避免一次拉全量。
    返回：方法列表 + 分页信息；内置工具同样会出现，便于统一治理。
    """
    return Envelope(data=catalog_service.list_methods(
        ctx, keyword=keyword, provider=provider, category_id=category_id, enabled=enabled, limit=limit, offset=offset,
    ))


@router.patch("/methods/{tool_name}", response_model=Envelope[dict], operation_id="capability_methods_update", summary="版本化维护工具方法分类与启停状态")
def methods_update(tool_name: str, payload: ToolMethodPatch, ctx: Context):
    """停用后该方法不再进入 AI、工作流或参数校验的新调用。"""
    return Envelope(data=catalog_service.update_method(ctx, tool_name, payload))


@router.post("/methods/{tool_name}/test", response_model=Envelope[dict], operation_id="capability_methods_test", summary="试运行已发布工具方法")
def methods_test(tool_name: str, payload: ToolArgumentsInput, ctx: Context):
    """通过生产工具网关试运行；HTTP/MCP 复用连接器版本中已保存的内网授权。"""
    return Envelope(data=service.test_method(ctx, tool_name, payload.arguments))


@router.post("/methods/batch", response_model=Envelope[dict], operation_id="capability_methods_batch_update", summary="原子批量维护工具方法")
def methods_batch_update(payload: ToolMethodBatchInput, ctx: Context):
    """POST /api/v1/capabilities/methods/batch：原子批量修改方法治理信息。

    整批要么全部生效要么全部回滚（服务层单事务提交），任一条 version 冲突或
    校验失败都会撤销本次全部改动，避免"改一半"的中间状态。
    """
    return Envelope(data=catalog_service.batch_update_methods(ctx, payload.items))


@router.get("/methods-export", operation_id="capability_methods_export", summary="导出工具方法目录 CSV")
def methods_export(ctx: Context):
    """GET /api/v1/capabilities/methods-export：导出工具方法目录为 CSV 附件（含 BOM）。"""
    return Response(
        content="\ufeff" + catalog_service.methods_csv(ctx),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tool-methods.csv"'},
    )


@router.post("/tools/{tool_name}/validate", response_model=Envelope[dict], operation_id="capability_tool_arguments_validate", summary="校验工具参数")
def tool_arguments_validate(tool_name: str, payload: ToolArgumentsInput, ctx: Context):
    """只做确定性 JSON Schema 校验，不调用模型，也不执行工具。"""
    return Envelope(data=service.validate_tool_arguments(ctx, tool_name, payload.arguments))


@router.post("/tools/{tool_name}/compile", response_model=Envelope[dict], operation_id="capability_tool_arguments_compile", summary="把自然语言需求编译为工具参数")
def tool_arguments_compile(tool_name: str, payload: ToolCompileInput, ctx: Context):
    """使用当前工作区模型配置生成参数并再次做确定性校验；完整记录一次真实运行。"""
    return Envelope(data=service.compile_tool_arguments(ctx, tool_name, payload.intent, payload.references))


@router.get("/connectors", response_model=Envelope[dict], operation_id="capability_connectors_list", summary="查询连接器")
def connectors_list(
    ctx: Context,
    keyword: str = Query("", max_length=200),
    provider: str = Query("", max_length=30),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
):
    """按 connectors.view 的角色数据范围返回，配置中的凭据永不返回明文。"""
    return Envelope(data=service.list_connectors(
        ctx, keyword=keyword, provider=provider, limit=limit, offset=offset,
    ))


@router.post("/connectors", response_model=Envelope[dict], status_code=201, operation_id="capability_connectors_create", summary="创建连接器草稿")
def connectors_create(payload: ConnectorInput, ctx: Context):
    """HTTP、数据库和 MCP 使用严格的分类型配置；认证只接受当前工作区密钥引用。"""
    return Envelope(data=service.create_connector(ctx, payload))


@router.get("/connectors/{connector_id}", response_model=Envelope[dict], operation_id="capability_connectors_get", summary="读取连接器及版本")
def connectors_get(connector_id: str, ctx: Context):
    """GET /api/v1/capabilities/connectors/{connector_id}：读取连接器及其版本列表。

    需要 connectors.view 且连接器在当前账号的数据范围内；返回的配置里凭据字段
    只回显引用标识（secret_id），绝不返回明文密钥。
    """
    return Envelope(data=service.get_connector(ctx, connector_id))


@router.post("/connectors/{connector_id}/versions", response_model=Envelope[dict], status_code=201, operation_id="capability_connector_versions_create", summary="创建连接器新版本")
def connector_versions_create(connector_id: str, payload: ConnectorVersionInput, ctx: Context):
    """已发布版本不可覆盖；修改始终形成可测试的新草稿版本。"""
    return Envelope(data=service.create_version(ctx, connector_id, payload))


@router.post("/connectors/{connector_id}/publish", response_model=Envelope[dict], operation_id="capability_connectors_publish", summary="发布连接器当前版本")
def connectors_publish(connector_id: str, payload: PublishInput, ctx: Context):
    """发布后工具网关只解析该版本；旧运行仍保留原版本标识。"""
    return Envelope(data=service.publish_connector(ctx, connector_id, payload.version))


@router.post("/connectors/{connector_id}/test", response_model=Envelope[dict], operation_id="capability_connectors_test", summary="测试指定连接器版本")
def connectors_test(connector_id: str, payload: ConnectorTestInput, ctx: Context):
    """测试也经过权限、Schema、密钥和统一工具网关，并生成 Run、Step、ToolCall 追踪。"""
    return Envelope(data=service.test_connector(
        ctx, connector_id, version_number=payload.version,
        tool_name=payload.tool_name, arguments=payload.arguments,
    ))


@router.post("/connectors/{connector_id}/clone", response_model=Envelope[dict], status_code=201, operation_id="capability_connectors_clone", summary="复制连接器为独立草稿")
def connectors_clone(connector_id: str, payload: ConnectorCloneInput, ctx: Context):
    """复制配置和工具 Schema；方法名使用新前缀，避免覆盖已发布调用契约。"""
    return Envelope(data=service.clone_connector(ctx, connector_id, payload))


@router.post("/connectors/{connector_id}/share", response_model=Envelope[dict], operation_id="capability_connectors_share", summary="修改连接器共享范围")
def connectors_share(connector_id: str, payload: ConnectorShareInput, ctx: Context):
    """POST /api/v1/capabilities/connectors/{connector_id}/share：调整连接器共享范围。

    共享只放宽"谁能看见/使用"，不改变数据源本身的数据范围绑定；改动需要连接器
    管理权限，由服务层校验并记录操作人。
    """
    return Envelope(data=service.share_connector(ctx, connector_id, payload))


@router.delete("/connectors/{connector_id}", response_model=Envelope[MutationResult], operation_id="capability_connectors_archive", summary="归档连接器")
def connectors_archive(connector_id: str, ctx: Context, version: int = Query(ge=1)):
    """归档不会物理删除版本和调用记录。"""
    service.archive_connector(ctx, connector_id, version)
    return Envelope(data=MutationResult(id=connector_id, version=version, deleted=True))


@router.post("/from-run", response_model=Envelope[dict], status_code=201, operation_id="capability_from_run_create", summary="从运行记录沉淀 Skill 或 Plugin")
def from_run_create(payload: FromRunInput, ctx: Context):
    """仅可读取当前账号 runs.view 数据范围内的运行；生成结果保持草稿状态。"""
    return Envelope(data=service.generate_from_run(ctx, payload.run_id, payload.target))
