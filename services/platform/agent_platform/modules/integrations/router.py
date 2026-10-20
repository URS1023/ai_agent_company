"""平台原生集成 API；配置和运行分开授权，返回真实持久化结果。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_platform.adapters.native import DifyAdapter, OpenWebUIAdapter, segment
from agent_platform.core.context import Actor, get_actor, require_permission
from agent_platform.core.database import get_session
from agent_platform.core.errors import DomainError
from agent_platform.core.files import FileService
from agent_platform.core.records import RecordStore
from agent_platform.core.settings import get_settings

from . import schemas as dto
from .models import NativeRun
from .service import (
    AUTOMATION, BINDING, IDENTITY, INTEGRATION, PROFILE, SPACE, TOOL, WORKSPACE_MAP,
    IntegrationService, run_dict,
)

router = APIRouter(prefix="/api/v1", tags=["原生应用集成"])


def _crud(path: str, kind: str, module: str, create_type: type[BaseModel], patch_type: type[BaseModel]) -> None:
    """配置资源共享生命周期与版本协议，业务执行仍有独立路由和服务。"""
    read = require_permission(f"{module}.read")
    write = require_permission(f"{module}.write")
    remove = require_permission(f"{module}.delete")

    def listing(request: Request, q: str = "", limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), actor: Actor = Depends(read), session: Session = Depends(get_session)):
        return RecordStore(session, actor).list(kind, q=q, limit=limit, offset=offset)

    def detail(id: str, actor: Actor = Depends(read), session: Session = Depends(get_session)):
        return RecordStore(session, actor).get(kind, id)

    def create(body, request: Request, actor: Actor = Depends(write), session: Session = Depends(get_session)):
        if kind == IDENTITY:
            actor.require("m14.admin")
        return IntegrationService(session, actor, request).create(kind, body.model_dump(exclude_none=True))

    def update(id: str, body, request: Request, actor: Actor = Depends(write), session: Session = Depends(get_session)):
        if kind == IDENTITY:
            actor.require("m14.admin")
        changes = body.model_dump(exclude_unset=True, exclude={"revision"})
        return IntegrationService(session, actor, request).update(kind, id, changes, body.revision)

    def delete(id: str, request: Request, revision: int = Query(..., ge=1), actor: Actor = Depends(remove), session: Session = Depends(get_session)):
        return IntegrationService(session, actor, request).delete(kind, id, revision)

    create.__annotations__["body"] = create_type
    update.__annotations__["body"] = patch_type
    for suffix, endpoint, methods, status in [
        ("", listing, ["GET"], 200), ("", create, ["POST"], 201),
        ("/{id}", detail, ["GET"], 200), ("/{id}", update, ["PATCH"], 200),
        ("/{id}", delete, ["DELETE"], 200),
    ]:
        router.add_api_route(path + suffix, endpoint, methods=methods, status_code=status, name=f"{kind}_{endpoint.__name__}")


_crud("/integration-instances", INTEGRATION, "m15", dto.IntegrationCreate, dto.IntegrationPatch)
_crud("/agent-app-bindings", BINDING, "m03", dto.BindingCreate, dto.BindingPatch)
_crud("/automations", AUTOMATION, "m04", dto.BindingCreate, dto.BindingPatch)
_crud("/knowledge-spaces", SPACE, "m08", dto.SpaceCreate, dto.SpacePatch)
_crud("/external-identities", IDENTITY, "m14", dto.IdentityCreate, dto.IdentityPatch)
_crud("/external-workspaces", WORKSPACE_MAP, "m14", dto.WorkspaceMapCreate, dto.WorkspaceMapPatch)
_crud("/model-profiles", PROFILE, "m15", dto.ModelProfileCreate, dto.ModelProfilePatch)
_crud("/tool-bindings", TOOL, "m12", dto.ToolCreate, dto.ToolPatch)


@router.post("/integrations/{id}/launches")
def launch(id: str, body: dto.LaunchInput, request: Request, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    targets = {"workflow": "m04.read", "agent": "m03.read", "agent_v2": "m03.read", "knowledge": "m08.read", "plugins": "m12.admin", "models": "m15.read", "tools": "m12.read", "admin": "m14.admin", "apps": "m13.read"}
    actor.require(targets.get(body.target, "m02.read"))
    return IntegrationService(session, actor, request).launch(id, body.target, body.native_id)


@router.post("/integration-instances/{id}/checks")
def check_integration(id: str, request: Request, actor: Actor = Depends(require_permission("m15.admin")), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    integration = service.integration(id)
    adapter = service.adapter(integration)
    result = adapter.parameters() if isinstance(adapter, DifyAdapter) else adapter.models()
    return {"integration_id": id, "available": True, "checked": "application_api" if integration["engine"] == "dify" else "model_catalog", "native_resource_count": len(result.get("data", [])) if isinstance(result, dict) else len(result)}


@router.get("/agent-app-bindings/{id}/releases")
def releases(id: str, actor: Actor = Depends(require_permission("m03.read")), session: Session = Depends(get_session)):
    binding = RecordStore(session, actor).get(BINDING, id)
    # 应用 Key 没有 Console 发布历史权限；只返回平台实际锁定的发布映射。
    return {"items": [{"id": binding["release_id"], "source": "platform_binding", "binding_revision": binding["revision"]}] if binding.get("release_id") else [], "total": int(bool(binding.get("release_id")))}


@router.post("/agent-app-bindings/{id}/invocations")
def invoke_agent(id: str, body: dto.InvocationInput, request: Request, actor: Actor = Depends(require_permission("m03.write")), session: Session = Depends(get_session)):
    return IntegrationService(session, actor, request).invoke(BINDING, id, body)


@router.post("/automations/{id}/invocations")
def invoke_automation(id: str, body: dto.InvocationInput, request: Request, actor: Actor = Depends(require_permission("m04.write")), session: Session = Depends(get_session)):
    return IntegrationService(session, actor, request).invoke(AUTOMATION, id, body)


@router.post("/automations/{id}/activations")
def activate(id: str, body: dto.ActivationInput, request: Request, actor: Actor = Depends(require_permission("m04.publish")), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    binding = service.store.get(AUTOMATION, id)
    if body.enabled:
        service.integration(binding["integration_id"])
        if not binding.get("release_id"):
            raise DomainError(409, "release_required", "请选择已发布的固定工作流版本")
    return service.update(AUTOMATION, id, {"enabled": body.enabled}, body.revision)


@router.get("/automations/{id}/runs")
def automation_runs(id: str, actor: Actor = Depends(require_permission("m04.read")), session: Session = Depends(get_session)):
    RecordStore(session, actor).get(AUTOMATION, id)
    rules = [NativeRun.workspace_id == actor.workspace_id, NativeRun.binding_id == id]
    if not actor.is_admin and not actor.can('m16.admin'):
        rules.append(NativeRun.user_id == actor.user_id)
    runs = session.scalars(select(NativeRun).where(*rules).order_by(NativeRun.created_at.desc()).limit(100)).all()
    return {"items": [run_dict(x) for x in runs], "total": len(runs)}


@router.get("/native-runs/{id}")
def read_run(id: str, request: Request, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    run = IntegrationService(session, actor, request).get_run(id)
    actor.require("m03.read" if run.binding_kind == BINDING else "m04.read")
    return run_dict(run)


@router.post("/native-runs/{id}/refreshes")
def refresh_run(id: str, request: Request, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    run = service.get_run(id)
    actor.require("m03.read" if run.binding_kind == BINDING else "m04.read")
    return service.refresh_run(id)


@router.post("/native-runs/{id}/stops")
def stop_run(id: str, request: Request, actor: Actor = Depends(get_actor), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    run = service.get_run(id)
    actor.require("m03.write" if run.binding_kind == BINDING else "m04.write")
    return service.stop_run(id)


@router.post("/integrations/{id}/callbacks")
async def callback(id: str, request: Request, workspace: str = Header(..., alias="X-Workspace-Id"), timestamp: str = Header(..., alias="X-Callback-Timestamp"), signature: str = Header(..., alias="X-Callback-Signature"), session: Session = Depends(get_session)):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 2_000_000:
            raise DomainError(413, "callback_too_large", "回调内容超过大小限制")
    actor = Actor(user_id="signed-callback", workspace_id=workspace, permissions=frozenset(), roles=())
    return IntegrationService(session, actor, request).callback(id, bytes(raw), timestamp, signature)


@router.post("/workflow-events")
def workflow_event(body: dto.WorkflowEventInput, request: Request, actor: Actor = Depends(require_permission("m04.write")), session: Session = Depends(get_session)):
    return IntegrationService(session, actor, request).invoke(AUTOMATION, body.automation_id, dto.InvocationInput(inputs=body.inputs, idempotency_key="event:" + body.event_id))


@router.get("/chat-context")
def chat_context(request: Request, actor: Actor = Depends(require_permission("m02.read")), session: Session = Depends(get_session)):
    store = RecordStore(session, actor)
    return {"workspace_id": actor.workspace_id, "knowledge_spaces": store.list(SPACE)["items"] if actor.can("m08.read") else [], "native_connections": [{"id": x["id"], "name": x["name"], "engine": x["engine"]} for x in store.list(INTEGRATION)["items"] if x.get("enabled", True)]}


@router.post("/chat-tool-invocations")
def chat_tool(body: dict[str, Any], request: Request, actor: Actor = Depends(require_permission("m02.write")), session: Session = Depends(get_session)):
    if body.get("tool") != "knowledge_search":
        raise DomainError(422, "chat_tool_unavailable", "该业务工具尚未登记")
    actor.require("m08.read")
    try:
        data = dto.SearchInput.model_validate(body.get("arguments", {}))
    except ValidationError as exc:
        raise DomainError(422, "tool_arguments_invalid", "知识检索参数不完整") from exc
    return IntegrationService(session, actor, request).search(data)


@router.get("/conversations/{id}/resources")
def conversation_resources(id: str, actor: Actor = Depends(require_permission("m02.read")), session: Session = Depends(get_session)):
    items = [x for x in RecordStore(session, actor).list("conversation_resource", limit=100)["items"] if x.get("conversation_id") == id and x.get("user_id") == actor.user_id]
    return {"items": items, "total": len(items)}


@router.get("/native-conversations")
def native_conversations(integration_id: str, request: Request, page: int = Query(1, ge=1, le=1000), actor: Actor = Depends(require_permission("m02.read")), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    integration = service.integration(integration_id)
    external_id, personal_ref = service.external_user(integration_id, require_token=True)
    if not personal_ref:
        raise DomainError(503, "native_personal_token_missing", "请关联当前用户的原生访问凭据")
    adapter = service.adapter(integration, credential_ref=personal_ref)
    if not isinstance(adapter, OpenWebUIAdapter):
        raise DomainError(422, "wrong_native_engine", "请选择 Open WebUI 服务")
    if adapter.current_user_id() != external_id:
        raise DomainError(403, "native_identity_mismatch", "原生访问凭据与当前账号映射不一致")
    return adapter.chats(page=page)


@router.post("/knowledge-spaces/{id}/document-imports", status_code=201)
def import_document(id: str, body: dto.ImportInput, request: Request, actor: Actor = Depends(require_permission("m08.write")), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    space = service.store.get(SPACE, id)
    if not space.get("enabled", True):
        raise DomainError(409, "knowledge_space_disabled", "该知识空间已停用")
    files = FileService(session, actor, get_settings(request))
    metadata = files.get(body.file_id)
    text = files.read_text(body.file_id)
    adapter = service.adapter(service.integration(space["integration_id"]), credential_ref=space.get("credential_ref"), dataset=True)
    result = adapter.import_text(space["native_id"], body.name or metadata["name"], text, indexing_technique=body.indexing_technique)
    document = result.get("document", {})
    if not document.get("id"):
        raise DomainError(502, "native_document_missing", "原生服务没有返回文档标识")
    return service.store.create("knowledge_document", {"space_id": id, "native_document_id": document["id"], "file_id": body.file_id, "name": document.get("name", metadata["name"]), "source_sha256": metadata.get("sha256"), "source_revision": metadata.get("revision"), "batch": result.get("batch"), "native": document})


@router.get("/knowledge-spaces/{id}/documents")
def documents(id: str, request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100), actor: Actor = Depends(require_permission("m08.read")), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    space = service.store.get(SPACE, id)
    adapter = service.adapter(service.integration(space["integration_id"]), credential_ref=space.get("credential_ref"), dataset=True)
    result = adapter.documents(space["native_id"], page=page, limit=limit)
    sources = {x["native_document_id"]: x for x in service.list_all("knowledge_document") if x["space_id"] == id}
    return {**result, "source_mappings": sources}


@router.post("/knowledge-searches")
def knowledge_search(body: dto.SearchInput, request: Request, actor: Actor = Depends(require_permission("m08.read")), session: Session = Depends(get_session)):
    return IntegrationService(session, actor, request).search(body)


@router.get("/knowledge-documents/{id}/citations")
def citations(id: str, request: Request, actor: Actor = Depends(require_permission("m08.read")), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    document = service.store.get("knowledge_document", id)
    items = [x for x in service.list_all("knowledge_citation") if x.get("space_id") == document["space_id"] and x.get("native_document_id") == document["native_document_id"]]
    return {"document": document, "items": items, "total": len(items)}


@router.get("/knowledge-citations/{id}")
def citation(id: str, actor: Actor = Depends(require_permission("m08.read")), session: Session = Depends(get_session)):
    return RecordStore(session, actor).get("knowledge_citation", id)


@router.post("/knowledge-documents/{id}/reindex-jobs")
def reindex(id: str, body: dto.ReindexInput, request: Request, actor: Actor = Depends(require_permission("m08.write")), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    document = service.store.get("knowledge_document", id)
    space = service.store.get(SPACE, document["space_id"])
    adapter = service.adapter(service.integration(space["integration_id"]), credential_ref=space.get("credential_ref"), dataset=True)
    result = adapter.reindex(space["native_id"], document["native_document_id"], body.process_rule)
    return service.store.create("knowledge_index_job", {"document_id": id, "result": result, "status": "submitted"})


@router.get("/knowledge-governance")
def governance(request: Request, actor: Actor = Depends(require_permission("m08.read")), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    documents = service.list_all("knowledge_document")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in documents:
        grouped.setdefault(f"{item['space_id']}:{item.get('name', '')}", []).append(item)
    conflicts = [{"key": k, "document_ids": [x["id"] for x in rows]} for k, rows in grouped.items() if len({x.get("source_sha256") for x in rows}) > 1]
    return {"documents": documents, "conflicts": conflicts, "total": len(documents)}


@router.post("/model-profiles/{id}/checks")
def check_model(id: str, request: Request, actor: Actor = Depends(require_permission("m15.admin")), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    profile = service.store.get(PROFILE, id)
    integration = service.integration(profile["integration_id"])
    adapter = service.adapter(integration, credential_ref=profile.get("credential_ref"))
    if not isinstance(adapter, OpenWebUIAdapter):
        raise DomainError(409, "native_console_required", "请在 Dify 模型供应商页面验证此模型")
    result = adapter.models()
    models = result.get("data", []) if isinstance(result, dict) else result
    available = any(x.get("id") == profile["native_model_id"] for x in models)
    return {"profile_id": id, "available": available, "scope": "catalog_visibility", "inference_tested": False}


@router.get("/model-usage")
def model_usage(actor: Actor = Depends(require_permission("m15.read")), session: Session = Depends(get_session)):
    runs = session.scalars(select(NativeRun).where(NativeRun.workspace_id == actor.workspace_id)).all()
    items = []
    for run in runs:
        data = run.result.get("data", run.result)
        if data.get("total_tokens") is not None or data.get("usage") is not None:
            items.append({"run_id": run.id, "integration_id": run.integration_id, "total_tokens": data.get("total_tokens"), "usage": data.get("usage")})
    items.extend(RecordStore(session, actor).list("model_usage_event", limit=100)["items"])
    return {"items": items, "total": len(items), "scope": "platform_invocations"}


@router.post("/tool-bindings/{id}/test-runs")
def tool_test(id: str, request: Request, actor: Actor = Depends(require_permission("m12.write")), session: Session = Depends(get_session)):
    service = IntegrationService(session, actor, request)
    tool = service.store.get(TOOL, id)
    service.validate_tool(tool)
    if not tool.get("enabled", True):
        raise DomainError(409, "tool_disabled", "该工具已停用")
    if tool["protocol"] == "openapi":
        if not tool.get("schema_document"):
            raise DomainError(422, "openapi_schema_required", "请先导入工具的 OpenAPI 描述")
        operations = [{"method": method.upper(), "path": path, "operation_id": operation.get("operationId")} for path, methods in tool["schema_document"]["paths"].items() for method, operation in methods.items() if method.lower() in {"get", "post", "put", "patch", "delete"} and isinstance(operation, dict)]
        return {"tool_id": id, "status": "schema_valid", "operations": operations, "executed": False}
    integration = service.integration(tool["integration_id"])
    # MCP握手走同一受控网络层，不启动本机子进程，也不执行tools/call。
    from agent_platform.adapters.native import NativeConnection, NativeHTTP
    ref = tool.get("credential_ref") or integration.get("credential_ref")
    client = NativeHTTP(NativeConnection(tool["endpoint"], service.secret(ref) if ref else "", allow_remote=service.settings.allow_remote_connections, auth_required=bool(ref)), transport=getattr(request.app.state, "native_http_transport", None))
    from agent_platform.adapters.mcp import check_mcp
    return {"tool_id": id, **check_mcp(client)}
