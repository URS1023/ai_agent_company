"""把平台权限、版本与任务证据连接到原生引擎，不复刻引擎内部功能。"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Request
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError as SchemaValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_platform.adapters.native import (
    DifyAdapter, NativeConnection, OpenWebUIAdapter, RetrievalInput,
    WorkflowInput, native_link, segment, validate_base_url,
)
from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError
from agent_platform.core.models import ResourceReference
from agent_platform.core.records import RecordStore
from agent_platform.core.settings import get_settings
from agent_platform.core.tasks import TaskService

from .models import CallbackReceipt, NativeRun
from .schemas import CallbackInput, InvocationInput, SearchInput


INTEGRATION = "integration_instance"
BINDING = "agent_app_binding"
AUTOMATION = "automation"
SPACE = "knowledge_space"
IDENTITY = "external_identity"
WORKSPACE_MAP = "external_workspace"
PROFILE = "model_profile"
TOOL = "tool_connection"
TERMINAL = {"succeeded", "failed", "cancelled"}
LAUNCH_PERMISSIONS = {
    "workflow": "m04.read", "agent": "m03.read", "agent_v2": "m03.read",
    "knowledge": "m08.read", "plugins": "m12.admin", "models": "m15.read",
    "tools": "m12.read", "admin": "m14.admin", "apps": "m13.read",
    "chat": "m02.read", "model_chat": "m02.read",
}
ENGINE_TARGETS = {
    "openwebui": {"chat", "model_chat", "models", "knowledge", "tools", "admin"},
    "dify": {"apps", "workflow", "agent", "agent_v2", "knowledge", "plugins", "models"},
}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def run_dict(run: NativeRun) -> dict[str, Any]:
    return {
        "id": run.id, "binding_id": run.binding_id, "integration_id": run.integration_id,
        "binding_revision": run.binding_revision, "release_id": run.release_id,
        "status": run.status, "native_run_id": run.native_run_id, "native_task_id": run.native_task_id,
        "task_id": run.platform_task_id, "sequence": run.sequence, "result": run.result,
        "created_at": run.created_at.isoformat(), "updated_at": run.updated_at.isoformat(),
    }


class IntegrationService:
    """一个请求内共享 SQLAlchemy 事务；所有列表和详情都从工作区仓储读取。"""

    def __init__(self, session: Session, actor: Actor, request: Request):
        self.session, self.actor, self.request = session, actor, request
        self.store = RecordStore(session, actor)
        self.settings = get_settings(request)

    def list_all(self, kind: str) -> list[dict[str, Any]]:
        # 只用于引用预检和映射查找；按页取完，不能因 UI 默认前 50 条漏掉引用。
        result: list[dict[str, Any]] = []
        offset = 0
        while True:
            batch = self.store.list(kind, limit=100, offset=offset)
            result.extend(batch["items"])
            offset += len(batch["items"])
            if not batch["items"] or offset >= batch["total"]:
                return result

    def validate_config(self, data: dict[str, Any], *, existing: dict[str, Any] | None = None) -> None:
        for key in ("base_url", "console_url", "endpoint"):
            if data.get(key):
                validate_base_url(data[key], allow_remote=self.settings.allow_remote_connections)
        for key in ("credential_ref", "dataset_credential_ref", "callback_secret_ref"):
            if data.get(key):
                self.secret(data[key])
        if data.get("native_id") and data.get("app_mode") != "openwebui_model":
            segment(data["native_id"])
        if data.get("release_id"):
            segment(data["release_id"])
        if data.get("input_schema") is not None:
            try:
                Draft202012Validator.check_schema(data["input_schema"])
            except SchemaError as exc:
                raise DomainError(422, "input_schema_invalid", "流程输入字段定义无效") from exc
        from .delegation_config import validate_delegation_config
        # PATCH可只替换一项凭据，成对绑定校验仍需合并旧配置，普通字段沿原增量校验。
        validate_delegation_config(self.session, self.actor, {**(existing or {}), **data},
            vault=getattr(self.request.app.state, 'secret_vault', None))
        from .chat_model_config import validate_chat_model_config
        validate_chat_model_config(self.actor, {**(existing or {}), **data}, existing=existing)

    def secret(self, ref: str | None) -> str:
        if not ref:
            raise DomainError(503, "integration_credential_missing", "请为该连接选择访问凭据")
        vault = getattr(self.request.app.state, "secret_vault", None)
        if vault is None:
            raise DomainError(503, "secret_store_unavailable", "凭据存储尚未就绪")
        try:
            return vault.get(ref, self.actor.workspace_id, session=self.session)
        except DomainError as exc:
            if exc.status == 404:
                raise DomainError(503, "integration_credential_missing", "该服务的访问凭据已不可用，请重新选择") from exc
            raise

    def integration(self, id: str) -> dict[str, Any]:
        item = self.store.get(INTEGRATION, id)
        if not item.get("enabled", True):
            raise DomainError(503, "integration_disabled", "该服务连接已停用")
        return item

    def adapter(self, integration: dict[str, Any], *, credential_ref: str | None = None, dataset: bool = False) -> DifyAdapter | OpenWebUIAdapter:
        ref = credential_ref or integration.get("dataset_credential_ref" if dataset else "credential_ref")
        connection = NativeConnection(
            base_url=integration["base_url"], token=self.secret(ref),
            timeout=integration.get("timeout_seconds", 30),
            allow_remote=self.settings.allow_remote_connections,
        )
        # 仅隔离测试注入 MockTransport；生产默认使用真正 HTTP 传输。
        transport = getattr(self.request.app.state, "native_http_transport", None)
        cls = DifyAdapter if integration["engine"] == "dify" else OpenWebUIAdapter
        return cls(connection, transport=transport)

    def create(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        self.validate_config(data)
        if data.get("integration_id"):
            integration = self.store.get(INTEGRATION, data["integration_id"])
            if kind in {SPACE, AUTOMATION} and integration["engine"] != "dify":
                raise DomainError(422, "wrong_native_engine", "该资源需要 Dify 服务连接")
            if kind == IDENTITY and any(x["integration_id"] == data["integration_id"] and x["platform_user_id"] == data["platform_user_id"] for x in self.list_all(IDENTITY)):
                raise DomainError(409, "identity_mapping_exists", "该用户已存在此服务的身份映射")
            if kind in {BINDING, AUTOMATION}:
                mode = data.get("app_mode", "workflow")
                if integration["engine"] == "openwebui" and mode != "openwebui_model":
                    raise DomainError(422, "wrong_native_engine", "Open WebUI 绑定请选择原生模型")
                if mode == "workflow" and not data.get("release_id"):
                    raise DomainError(422, "release_required", "请选择已发布的固定工作流版本", {"release_id": "必填"})
                if kind == AUTOMATION and mode != "workflow":
                    raise DomainError(422, "workflow_binding_required", "自动化需要绑定已发布的工作流")
        if kind == TOOL:
            self.validate_tool(data)
        return self.store.create(kind, data)

    def update(self, kind: str, id: str, data: dict[str, Any], revision: int) -> dict[str, Any]:
        old = self.store.get(kind, id)
        required = {"name", "base_url", "console_url", "native_id", "enabled", "timeout_seconds", "external_user_id", "external_workspace_id", "native_model_id", "parameters"}
        invalid = {key: "不能为空" for key, value in data.items() if key in required and value is None}
        if invalid:
            raise DomainError(422, "required_field_empty", "请补全必填字段", invalid)
        self.validate_config({**data, "app_mode": old.get("app_mode")}, existing=old)
        merged = {**old, **data}
        if kind in {BINDING, AUTOMATION} and merged.get("app_mode") == "workflow" and not merged.get("release_id"):
            raise DomainError(422, "release_required", "请选择已发布的固定工作流版本")
        if kind == TOOL:
            self.validate_tool(merged)
        return self.store.update(kind, id, data, expected_revision=revision)

    def delete(self, kind: str, id: str, revision: int) -> dict[str, Any]:
        self.store.get(kind, id)
        if kind == INTEGRATION:
            for dependent in (BINDING, AUTOMATION, SPACE, IDENTITY, WORKSPACE_MAP, PROFILE, TOOL):
                if any(x.get("integration_id") == id for x in self.list_all(dependent)):
                    raise DomainError(409, "integration_in_use", "该连接仍有关联资源，请先停用或解除关联")
        if kind == SPACE and any(x.get("space_id") == id for x in self.list_all("knowledge_document")):
            raise DomainError(409, "knowledge_space_in_use", "该知识空间仍有已导入文档，请先停用或迁移资料")
        self.store.delete(kind, id, expected_revision=revision)
        return {"id": id, "deleted": True}

    def credential_references(self, ref: str) -> list[dict[str, Any]]:
        """供凭据删除前检查；运行快照保留未终态调用的真实资源和凭据关联。"""
        references = []
        keys = {"credential_ref", "dataset_credential_ref", "callback_secret_ref", "delegation_jwt_secret_ref"}
        for kind in (INTEGRATION, BINDING, AUTOMATION, SPACE, IDENTITY, PROFILE, TOOL):
            for item in self.list_all(kind):
                if any(item.get(key) == ref for key in keys):
                    references.append({"kind": kind, "id": item["id"], "name": item.get("name", "")})
        runs = self.session.scalars(select(NativeRun).where(NativeRun.workspace_id == self.actor.workspace_id, NativeRun.status.not_in(TERMINAL)))
        for run in runs:
            if any(value.get(key) == ref for value in run.snapshot.values() if isinstance(value, dict) for key in keys):
                references.append({"kind": "native_run", "id": run.id, "name": run.status})
        # 独立账号等领域实体以显式引用登记凭据，不能只扫描目录JSON后允许删除。
        linked = self.session.scalars(select(ResourceReference).where(
            ResourceReference.workspace_id == self.actor.workspace_id,
            ResourceReference.target_type == 'secret', ResourceReference.target_id == ref))
        known = {(item['kind'], item['id']) for item in references}
        for link in linked:
            if (link.source_type, link.source_id) not in known:
                references.append({'kind': link.source_type, 'id': link.source_id, 'name': ''})
                known.add((link.source_type, link.source_id))
        return references

    def external_user(self, integration_id: str, *, require_token: bool = False) -> tuple[str, str | None]:
        mapping = next((x for x in self.list_all(IDENTITY) if x["integration_id"] == integration_id and x["platform_user_id"] == self.actor.user_id and x.get("enabled", True)), None)
        if not mapping and require_token:
            raise DomainError(503, "native_identity_unmapped", "请先关联当前用户的原生账号")
        if mapping:
            return mapping["external_user_id"], mapping.get("credential_ref")
        # Dify Service API 的 end-user 本就是应用内 opaque ID，不是 Console 账号。
        return "platform-" + hashlib.sha256(f"{self.actor.workspace_id}:{self.actor.user_id}".encode()).hexdigest(), None

    def native_entries(self, target: str) -> dict[str, Any]:
        """提供按使用权限过滤的入口，不要求员工拥有连接管理权或暴露凭据引用。"""
        permission = LAUNCH_PERMISSIONS.get(target)
        if permission is None:
            raise DomainError(422, "invalid_native_target", "该原生页面入口不存在")
        self.actor.require(permission)
        items = [
            {"id": row["id"], "name": row["name"], "engine": row["engine"], "enabled": True}
            for row in self.list_all(INTEGRATION)
            if row.get("enabled", True) and target in ENGINE_TARGETS.get(row.get("engine"), set())
        ]
        return {"items": items, "total": len(items)}

    def launch(self, integration_id: str, target: str, native_id: str | None) -> dict[str, Any]:
        permission = LAUNCH_PERMISSIONS.get(target)
        if permission is None:
            raise DomainError(422, "invalid_native_target", "该原生页面入口不存在")
        self.actor.require(permission)
        integration = self.integration(integration_id)
        url = native_link(integration["engine"], integration["console_url"], target, native_id, allow_remote=self.settings.allow_remote_connections)
        return {"url": url, "requires_native_login": True, "engine": integration["engine"], "integration_id": integration_id}

    def invoke(self, kind: str, binding_id: str, data: InvocationInput) -> dict[str, Any]:
        binding = self.store.get(kind, binding_id)
        if not binding.get("enabled", True):
            raise DomainError(409, "binding_disabled", "该应用绑定已暂停")
        integration = self.integration(binding["integration_id"])
        mode = binding.get("app_mode", "workflow")
        if mode in {"agent_v2", "openwebui_model"}:
            target = "agent_v2" if mode == "agent_v2" else "model_chat"
            raise DomainError(409, "native_console_required", "请在原生应用中运行此智能体", {"launch_url": self.launch(integration["id"], target, binding["native_id"])["url"]})
        digest = _hash(data.model_dump(exclude={"idempotency_key"}))
        previous = self.session.scalar(select(NativeRun).where(NativeRun.workspace_id == self.actor.workspace_id, NativeRun.binding_id == binding_id, NativeRun.idempotency_key == data.idempotency_key))
        if previous:
            if previous.request_hash != digest or previous.user_id != self.actor.user_id:
                raise DomainError(409, "idempotency_conflict", "该请求标识已经用于另一项输入")
            return run_dict(previous)
        adapter = self.adapter(integration, credential_ref=binding.get("credential_ref"))
        if not isinstance(adapter, DifyAdapter):
            raise DomainError(422, "wrong_native_engine", "该调用需要 Dify 应用连接")
        external_user, _ = self.external_user(integration["id"])
        if mode == "workflow" and not binding.get("release_id"):
            raise DomainError(409, "release_required", "该工作流尚未绑定发布版本")
        if mode != "workflow" and not data.query:
            raise DomainError(422, "query_required", "请输入消息内容", {"query": "必填"})
        if binding.get("input_schema"):
            try:
                Draft202012Validator(binding["input_schema"]).validate(data.inputs)
            except SchemaValidationError as exc:
                raise DomainError(422, "workflow_inputs_invalid", "输入不符合该发布流程的字段要求") from exc
        task_service = TaskService(self.session, self.actor)
        task = task_service.create("native_workflow" if mode == "workflow" else "native_agent", binding["name"], inputs={"binding_id": binding_id, "release_id": binding.get("release_id")}, engine="dify", idempotency_key=f"{binding_id}:{data.idempotency_key}")
        run = NativeRun(
            workspace_id=self.actor.workspace_id, user_id=self.actor.user_id,
            integration_id=integration["id"], binding_id=binding_id, binding_kind=kind,
            binding_revision=binding["revision"], release_id=binding.get("release_id"),
            idempotency_key=data.idempotency_key, request_hash=digest, status="queued",
            platform_task_id=task["id"], external_user_id=external_user,
            snapshot={"binding": binding, "integration": integration}, result={},
        )
        self.session.add(run)
        # 网络边界之前单独提交幂等预约。进程崩溃或客户端刷新不能把同键再次发送上游。
        # 原生协议没有通用幂等键，不跨网络承诺 exactly-once；未知状态只允许核对。
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            previous = self.session.scalar(select(NativeRun).where(NativeRun.workspace_id == self.actor.workspace_id, NativeRun.binding_id == binding_id, NativeRun.idempotency_key == data.idempotency_key))
            if previous and previous.request_hash == digest and previous.user_id == self.actor.user_id:
                return run_dict(previous)
            raise DomainError(409, "idempotency_conflict", "该请求标识已经用于另一项输入")
        run.status = "running"
        task_service.transition(task["id"], "running")
        self.session.commit()
        try:
            if mode == "workflow":
                result = adapter.run_workflow(WorkflowInput(inputs=data.inputs), external_user, release_id=binding["release_id"])
            else:
                result = adapter.chat(data.query or "", external_user, inputs=data.inputs, conversation_id=data.conversation_id)
        except DomainError as exc:
            code = getattr(exc, "code", "native_request_failed")
            status = "unknown" if code in {"native_timeout", "native_unavailable", "native_invalid_response"} else "failed"
            run.status, run.result = status, {"error": {"code": code, "message": "原生请求结果需要核对" if status == "unknown" else "原生服务拒绝了该请求"}}
            task_service.transition(task["id"], status, error=run.result["error"]["message"])
            task_service.event(task["id"], "native_request_error", {"run_id": run.id, "code": code, "status": status})
            self.session.commit()
            raise DomainError(getattr(exc, "status", 503), code, "执行状态未知，请核对原生运行记录后处理" if status == "unknown" else "原生服务调用失败", {"run_id": run.id, "task_id": task["id"]}) from exc
        response_data = result.get("data") if isinstance(result.get("data"), dict) else result
        native_status = response_data.get("status", "succeeded" if result.get("answer") is not None else "running")
        status = {"stopped": "cancelled", "paused": "waiting_review"}.get(native_status, native_status)
        if status not in {"running", "succeeded", "failed", "cancelled", "waiting_review"}:
            status = "unknown"
        run.status, run.result = status, result
        run.native_run_id = result.get("workflow_run_id") or response_data.get("id") or result.get("message_id")
        run.native_task_id = result.get("task_id")
        if status != "running":
            self.transition_task(task["id"], status, outputs=response_data.get("outputs"), error=response_data.get("error"), source_run_id=run.native_run_id)
        task_service.event(task["id"], "native_response", {"run_id": run.id, "native_run_id": run.native_run_id, "status": status})
        self.store.audit("native.invoke", kind, binding_id, {"run_id": run.id, "task_id": task["id"], "release_id": run.release_id})
        self.session.commit()
        return run_dict(run)

    def get_run(self, id: str, *, callback_verified: bool = False) -> NativeRun:
        run = self.session.scalar(select(NativeRun).where(NativeRun.id == id, NativeRun.workspace_id == self.actor.workspace_id))
        if not run or (not callback_verified and run.user_id != self.actor.user_id
                       and not self.actor.is_admin and not self.actor.can('m16.admin')):
            raise DomainError(404, "native_run_not_found", "运行记录不存在")
        return run

    def refresh_run(self, id: str) -> dict[str, Any]:
        run = self.get_run(id)
        if run.binding_kind not in {AUTOMATION, BINDING} or not run.native_run_id:
            raise DomainError(409, "native_run_unavailable", "该记录没有可查询的原生运行标识")
        binding = run.snapshot.get("binding") or self.store.get(run.binding_kind, run.binding_id)
        if binding.get("app_mode") != "workflow":
            raise DomainError(409, "workflow_run_required", "该接口用于查询工作流运行")
        adapter = self.adapter(run.snapshot.get("integration") or self.integration(run.integration_id), credential_ref=binding.get("credential_ref"))
        result = adapter.workflow_run(run.native_run_id)
        status = {"stopped": "cancelled", "paused": "waiting_review"}.get(result.get("status"), result.get("status", "unknown"))
        if run.status not in TERMINAL and status in TERMINAL | {"running", "waiting_review"}:
            run.status, run.result = status, result
            if run.platform_task_id:
                self.transition_task(run.platform_task_id, status, outputs=result.get("outputs"), error=result.get("error"), source_run_id=run.native_run_id)
            self.session.flush()
        return run_dict(run)

    def stop_run(self, id: str) -> dict[str, Any]:
        run = self.get_run(id)
        if run.status in TERMINAL:
            return run_dict(run)
        if run.user_id != self.actor.user_id:
            self.actor.require("m03.admin" if run.binding_kind == BINDING else "m04.admin")
        if not run.native_task_id:
            raise DomainError(409, "native_task_unavailable", "原生服务没有返回可停止的任务标识")
        binding = run.snapshot.get("binding") or self.store.get(run.binding_kind, run.binding_id)
        adapter = self.adapter(run.snapshot.get("integration") or self.integration(run.integration_id), credential_ref=binding.get("credential_ref"))
        adapter.stop_workflow(run.native_task_id, run.external_user_id)
        # stop API 确认的是停止请求已受理，实际终态由查询或签名事件确认。
        if run.platform_task_id:
            TaskService(self.session, self.actor).transition(run.platform_task_id, "cancel_requested")
        run.status = "cancel_requested"
        self.session.flush()
        return run_dict(run)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        """根任务入口按平台 task_id 委托取消；缺少原生 task_id 时拒绝假停止。"""
        run = self.session.scalar(select(NativeRun).where(
            NativeRun.workspace_id == self.actor.workspace_id, NativeRun.platform_task_id == task_id,
        ))
        if not run:
            raise DomainError(409, "native_task_unmapped", "该任务没有原生运行关联")
        return self.stop_run(run.id)

    def transition_task(self, task_id: str, status: str, **kwargs: Any) -> None:
        """原生可直接到取消终态，平台先记录取消请求以维持统一状态机。"""
        service = TaskService(self.session, self.actor)
        current = service.get(task_id)["status"]
        if status == "cancelled" and current == "running":
            service.transition(task_id, "cancel_requested")
        service.transition(task_id, status, **kwargs)

    def search(self, data: SearchInput) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        for id in dict.fromkeys(data.space_ids):
            space = self.store.get(SPACE, id)
            if not space.get("enabled", True):
                raise DomainError(409, "knowledge_space_disabled", "所选知识空间已停用")
            adapter = self.adapter(self.integration(space["integration_id"]), credential_ref=space.get("credential_ref"), dataset=True)
            if not isinstance(adapter, DifyAdapter):
                raise DomainError(422, "wrong_native_engine", "知识检索需要 Dify 数据集连接")
            result = adapter.retrieve(space["native_id"], RetrievalInput(query=data.query, retrieval_model={"search_method": "semantic_search", "reranking_enable": False, "top_k": data.top_k, "score_threshold_enabled": True, "score_threshold": data.score_threshold}))
            for item in result.get("records", []):
                source_segment = item.get("segment", {})
                native_doc = source_segment.get("document", {})
                document_id = source_segment.get("document_id") or native_doc.get("id")
                citation = self.store.create("knowledge_citation", {
                    "space_id": id, "native_document_id": document_id,
                    "segment_id": source_segment.get("id"), "position": source_segment.get("position"),
                    "content": source_segment.get("content", ""), "score": item.get("score"),
                    "document_name": native_doc.get("name"),
                })
                records.append({**item, "space_id": id, "citation_id": citation["id"]})
        records.sort(key=lambda x: x.get("score") or 0, reverse=True)
        return {"records": records[:data.top_k], "query": data.query}

    def model_completion(self, profile_id: str, messages: list[dict[str, str]], *, max_tokens: int = 1200) -> dict[str, Any]:
        """领域服务使用已授权模型配置生成一次候选结果；后续业务校验由调用域负责。"""
        if not profile_id:
            raise DomainError(503, "model_not_configured", "请先选择该任务使用的模型配置")
        profile = self.store.get(PROFILE, profile_id)
        if not profile.get("enabled", True) or profile.get("role") not in {"chat", "reasoning"}:
            raise DomainError(503, "model_profile_unavailable", "该模型配置不可用于文本生成任务")
        integration = self.integration(profile["integration_id"])
        adapter = self.adapter(integration, credential_ref=profile.get("credential_ref"))
        if not isinstance(adapter, OpenWebUIAdapter):
            raise DomainError(503, "model_gateway_unavailable", "该任务需要已配置的 Open WebUI 模型连接")
        result = adapter.completion(profile["native_model_id"], messages, max_tokens=max_tokens, temperature=0)
        usage = result.get("usage")
        self.store.create("model_usage_event", {
            "profile_id": profile_id, "model_id": profile["native_model_id"],
            "integration_id": integration["id"], "usage": usage,
            "operation": "single_completion", "user_id": self.actor.user_id,
        })
        return result

    def callback(self, integration_id: str, raw_body: bytes, timestamp: str, signature: str) -> dict[str, Any]:
        integration = self.integration(integration_id)
        try:
            stamp = int(timestamp)
        except (ValueError, TypeError) as exc:
            raise DomainError(401, "callback_timestamp_invalid", "回调时间戳无效") from exc
        if abs(time.time() - stamp) > 300:
            raise DomainError(401, "callback_expired", "回调签名已过期")
        secret = self.secret(integration.get("callback_secret_ref"))
        expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.removeprefix("sha256="), expected):
            raise DomainError(401, "callback_signature_invalid", "回调签名无效")
        try:
            event = CallbackInput.model_validate_json(raw_body)
        except ValueError as exc:
            raise DomainError(422, "callback_payload_invalid", "回调事件格式错误") from exc
        body_hash = hashlib.sha256(raw_body).hexdigest()
        previous = self.session.scalar(select(CallbackReceipt).where(CallbackReceipt.integration_id == integration_id, CallbackReceipt.event_id == event.event_id))
        if previous:
            if previous.body_hash != body_hash:
                raise DomainError(409, "callback_event_conflict", "同一事件标识不能对应不同内容")
            return {"event_id": event.event_id, "disposition": previous.disposition, "duplicate": True}
        run = self.get_run(event.run_id, callback_verified=True)
        if run.integration_id != integration_id or run.native_run_id != event.native_run_id:
            raise DomainError(409, "callback_run_mismatch", "事件不属于该原生运行")
        disposition = "applied"
        if event.sequence <= run.sequence:
            disposition = "stale"
        elif run.status in TERMINAL:
            disposition = "terminal_ignored"
        else:
            # CAS 同时约束序号与终态，避免并发回调在先读后写之间覆盖更新。
            changed = self.session.execute(update(NativeRun).where(
                NativeRun.id == run.id, NativeRun.sequence < event.sequence,
                NativeRun.status.not_in(TERMINAL) if run.status not in TERMINAL else NativeRun.status == event.status,
            ).values(status=event.status, sequence=event.sequence, result={**run.result, "callback": event.model_dump(exclude_none=True)}))
            if not changed.rowcount:
                disposition = "stale"
            self.session.refresh(run)
            if changed.rowcount and run.platform_task_id:
                # 签名回调只更新已绑定的运行/任务；内部状态迁移限制在原操作者范围。
                callback_actor = Actor(user_id=run.user_id, workspace_id=run.workspace_id,
                    permissions=frozenset({'m03.read' if run.binding_kind == BINDING else 'm04.read'}), roles=())
                IntegrationService(self.session, callback_actor, self.request).transition_task(
                    run.platform_task_id, event.status, outputs=event.outputs, error=event.error, source_run_id=event.native_run_id)
        self.session.add(CallbackReceipt(integration_id=integration_id, workspace_id=self.actor.workspace_id, event_id=event.event_id, body_hash=body_hash, run_id=run.id, disposition=disposition))
        self.session.flush()
        return {"event_id": event.event_id, "disposition": disposition, "duplicate": False}

    def validate_tool(self, data: dict[str, Any]) -> None:
        """只接入 OpenAPI 与 MCP HTTP；不接受 Python、shell 或 stdio 命令执行配置。"""
        validate_base_url(data["endpoint"], allow_remote=self.settings.allow_remote_connections)
        schema = data.get("schema_document")
        if data["protocol"] == "openapi" and schema is not None:
            if not str(schema.get("openapi", "")).startswith("3.") or not isinstance(schema.get("paths"), dict):
                raise DomainError(422, "invalid_openapi_document", "请提供 OpenAPI 3 格式的工具描述")
            for path, operations in schema["paths"].items():
                if not str(path).startswith("/") or not isinstance(operations, dict):
                    raise DomainError(422, "invalid_openapi_document", "工具接口路径格式错误")
            if len(json.dumps(schema)) > 2_000_000:
                raise DomainError(422, "openapi_document_too_large", "工具描述超过大小限制")
