"""Dify 1.17.1 与 Open WebUI 0.12.3 的受限、可替换 HTTP 适配器。

协议依据本机参考项目对应控制器核查，调用不重试、不追随重定向。
适配器只执行已登记的 API，不接受任意 URL/路径，也不代建原生对话循环。
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Any, Iterator, Literal
from urllib.parse import quote, urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agent_platform.core.errors import DomainError


def validate_base_url(url: str, *, allow_remote: bool = False) -> str:
    """校验显式登记的服务地址；开发默认仅接受字面回环地址，避免 DNS 重绑定。"""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DomainError(422, "invalid_integration_url", "请输入完整的 HTTP 或 HTTPS 服务地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DomainError(422, "invalid_integration_url", "服务地址不能包含凭据、查询参数或片段")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if not allow_remote and not (address and address.is_loopback):
        raise DomainError(422, "remote_connection_disabled", "当前环境只允许连接本机服务")
    if address and (address.is_unspecified or address.is_multicast):
        raise DomainError(422, "invalid_integration_url", "该服务地址不可用")
    try:
        parsed.port
    except ValueError as exc:
        raise DomainError(422, "invalid_integration_url", "端口格式错误") from exc
    return url.rstrip("/")


def segment(value: str) -> str:
    """所有上游资源标识作为单一路径片段编码，阻止绑定配置越过路由边界。"""
    if not value or value in {".", ".."} or any(c in value for c in "/\\?#\x00\r\n"):
        raise DomainError(422, "invalid_native_id", "原生资源标识格式错误")
    return quote(value, safe="")


class WorkflowInput(BaseModel):
    """Dify 发布工作流输入；用户标识由平台映射生成，不接受前端覆盖。"""

    model_config = ConfigDict(extra="forbid")
    inputs: dict[str, Any] = Field(default_factory=dict)
    files: list[dict[str, Any]] | None = None
    response_mode: Literal["blocking", "streaming"] = "blocking"


class RetrievalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=8000)
    retrieval_model: dict[str, Any] | None = None


@dataclass(frozen=True)
class NativeConnection:
    base_url: str
    token: str
    timeout: float = 30.0
    allow_remote: bool = False
    auth_required: bool = True


class NativeHTTP:
    """集中处理上游错误并抑制响应中的凭据/内部错误详情。"""

    def __init__(self, connection: NativeConnection, *, transport: httpx.BaseTransport | None = None):
        self.connection = connection
        self.base_url = validate_base_url(connection.base_url, allow_remote=connection.allow_remote)
        if connection.auth_required and not connection.token.strip():
            raise DomainError(503, "integration_credential_missing", "请配置该服务的访问凭据")
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url + "/", timeout=httpx.Timeout(self.connection.timeout, connect=5.0),
            follow_redirects=False, trust_env=False, transport=self.transport,
            headers={**({"Authorization": f"Bearer {self.connection.token}"} if self.connection.token else {}), "Accept": "application/json"},
        )

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        if response.status_code in {401, 403}:
            raise DomainError(502, "native_permission_denied", "原生服务拒绝访问，请检查凭据类型与资源权限")
        if response.status_code == 404:
            raise DomainError(502, "native_resource_missing", "原生资源不存在或当前版本不支持此接口")
        if response.status_code == 429:
            raise DomainError(503, "native_rate_limited", "原生服务繁忙，请稍后再试")
        raise DomainError(502, "native_request_failed", f"原生服务调用失败（HTTP {response.status_code}）")

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        try:
            with self._client() as client:
                response = client.request(method, path.lstrip("/") if path else self.base_url, **kwargs)
                self._check(response)
                if response.status_code == 204:
                    return {}
                data = response.json()
                if not isinstance(data, (dict, list)):
                    raise ValueError("unexpected response")
                return data
        except httpx.TimeoutException as exc:
            raise DomainError(504, "native_timeout", "原生服务响应超时，执行状态需要核对后再重试") from exc
        except httpx.RequestError as exc:
            raise DomainError(503, "native_unavailable", "无法连接原生服务，请检查地址和服务状态") from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise DomainError(502, "native_invalid_response", "原生服务返回了无法识别的响应") from exc

    def stream(self, path: str, payload: dict[str, Any]) -> Iterator[bytes]:
        """原样转发 SSE；上下文随迭代器关闭，取消订阅时释放连接，不伪造终止事件。"""
        try:
            with self._client() as client, client.stream("POST", path.lstrip("/"), json=payload) as response:
                self._check(response)
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raise DomainError(502, "native_invalid_stream", "原生服务未返回事件流")
                yield from response.iter_bytes()
        except httpx.TimeoutException as exc:
            raise DomainError(504, "native_timeout", "原生事件流响应超时") from exc
        except httpx.RequestError as exc:
            raise DomainError(503, "native_unavailable", "原生事件流连接已中断") from exc


class DifyAdapter(NativeHTTP):
    """应用 Key 和 Dataset Key 必须分别构造；不能调用 Console API。"""

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        result = super().request(method, path, **kwargs)
        if not isinstance(result, dict):
            raise DomainError(502, "native_invalid_response", "Dify 服务返回了无法识别的响应")
        return result

    def run_workflow(self, data: WorkflowInput, user: str, *, release_id: str) -> dict[str, Any]:
        # 锁定发布版本不能退回 /workflows/run 的“当前版本”，否则无法追溯业务证据。
        payload = data.model_dump(exclude_none=True)
        payload["user"] = user
        result = self.request("POST", f"workflows/{segment(release_id)}/run", json=payload)
        if not result.get("workflow_run_id") or not isinstance(result.get("data"), dict) or not result["data"].get("status"):
            raise DomainError(502, "native_invalid_response", "Dify 没有返回有效的工作流运行记录")
        return result

    def stream_workflow(self, data: WorkflowInput, user: str, *, release_id: str) -> Iterator[bytes]:
        payload = data.model_dump(exclude_none=True)
        payload.update(user=user, response_mode="streaming")
        return self.stream(f"workflows/{segment(release_id)}/run", payload)

    def workflow_run(self, run_id: str) -> dict[str, Any]:
        return self.request("GET", f"workflows/run/{segment(run_id)}")  # type: ignore[return-value]

    def stop_workflow(self, task_id: str, user: str) -> dict[str, Any]:
        return self.request("POST", f"workflows/tasks/{segment(task_id)}/stop", json={"user": user})  # type: ignore[return-value]

    def parameters(self) -> dict[str, Any]:
        return self.request("GET", "parameters")  # type: ignore[return-value]

    def chat(self, query: str, user: str, *, inputs: dict[str, Any] | None = None, conversation_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"inputs": inputs or {}, "query": query, "user": user, "response_mode": "blocking"}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return self.request("POST", "chat-messages", json=payload)  # type: ignore[return-value]

    def import_text(self, dataset_id: str, name: str, text: str, *, indexing_technique: str = "high_quality", process_rule: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("POST", f"datasets/{segment(dataset_id)}/document/create-by-text", json={
            "name": name, "text": text, "indexing_technique": indexing_technique,
            "process_rule": process_rule or {"mode": "automatic"}, "doc_language": "Chinese",
        })  # type: ignore[return-value]

    def documents(self, dataset_id: str, *, page: int = 1, limit: int = 20) -> dict[str, Any]:
        return self.request("GET", f"datasets/{segment(dataset_id)}/documents", params={"page": page, "limit": limit})  # type: ignore[return-value]

    def retrieve(self, dataset_id: str, data: RetrievalInput) -> dict[str, Any]:
        return self.request("POST", f"datasets/{segment(dataset_id)}/retrieve", json=data.model_dump(exclude_none=True))  # type: ignore[return-value]

    def reindex(self, dataset_id: str, document_id: str, process_rule: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"datasets/{segment(dataset_id)}/documents/{segment(document_id)}/update-by-text", json={"process_rule": process_rule})  # type: ignore[return-value]

    def delete_document(self, dataset_id: str, document_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"datasets/{segment(dataset_id)}/documents/{segment(document_id)}")  # type: ignore[return-value]


class OpenWebUIAdapter(NativeHTTP):
    """只使用 Open WebUI 原生 REST 协议；聊天界面/消息树由原服务保留。"""

    def models(self) -> dict[str, Any] | list[Any]:
        return self.request("GET", "api/models")

    def current_user_id(self) -> str:
        # 不把 /auths/ 返回的token等字段透传给平台浏览器。
        result = self.request("GET", "api/v1/auths/")
        if not isinstance(result, dict) or not isinstance(result.get("id"), str):
            raise DomainError(502, "native_identity_invalid", "原生服务没有返回有效用户身份")
        return result["id"]

    def model_presets(self) -> list[Any] | dict[str, Any]:
        return self.request("GET", "api/v1/models/")

    def completion(self, model_id: str, messages: list[dict[str, str]], *, max_tokens: int = 1200, temperature: float = 0) -> dict[str, Any]:
        """单次原生模型推理；用于受控生成任务，不自建消息树或聊天循环。"""
        if not model_id or not 1 <= max_tokens <= 4096 or not 0 <= temperature <= 1:
            raise DomainError(422, "model_request_invalid", "模型或生成预算无效")
        if not messages or any(x.get("role") not in {"system", "user", "assistant"} or not isinstance(x.get("content"), str) for x in messages):
            raise DomainError(422, "model_messages_invalid", "模型消息格式错误")
        if sum(len(x["content"]) for x in messages) > 80000:
            raise DomainError(422, "model_prompt_too_large", "当前任务上下文超过生成预算")
        result = self.request("POST", "api/chat/completions", json={
            "model": model_id, "messages": messages, "stream": False,
            "max_tokens": max_tokens, "temperature": temperature, "tool_ids": [],
        })
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list) or not result["choices"]:
            raise DomainError(502, "native_invalid_model_response", "原生模型没有返回有效生成结果")
        first = result["choices"][0]
        if not isinstance(first, dict) or not isinstance(first.get("message"), dict) or not isinstance(first["message"].get("content"), str) or not first["message"]["content"].strip():
            raise DomainError(502, "native_invalid_model_response", "原生模型没有返回文本生成结果")
        return result

    def chats(self, *, page: int = 1) -> list[Any] | dict[str, Any]:
        return self.request("GET", "api/v1/chats/", params={"page": page})

    def chat_detail(self, chat_id: str) -> dict[str, Any]:
        return self.request("GET", f"api/v1/chats/{segment(chat_id)}")  # type: ignore[return-value]

    def delete_chat(self, chat_id: str) -> dict[str, Any] | list[Any]:
        return self.request("DELETE", f"api/v1/chats/{segment(chat_id)}")


def native_link(engine: str, console_url: str, target: str, native_id: str | None = None, *, allow_remote: bool = False) -> str:
    """构造真实原生页面链接；不附带 API Key，不声称已建立 Console 会话。"""
    base = validate_base_url(console_url, allow_remote=allow_remote)
    if engine == "openwebui" and target == "model_chat":
        if not native_id:
            raise DomainError(422, "native_model_required", "请选择原生模型")
        return base + "/?" + urlencode({"model": native_id})
    value = segment(native_id) if native_id else None
    if engine == "openwebui":
        paths = {"chat": f"/c/{value}" if value else "/", "models": "/workspace/models", "knowledge": "/workspace/knowledge", "tools": "/workspace/tools", "admin": "/admin"}
    else:
        paths = {"apps": "/apps", "workflow": f"/app/{value}/workflow" if value else "/apps", "agent": f"/app/{value}/configuration" if value else "/apps", "agent_v2": f"/agents/{value}" if value else "/agents", "knowledge": f"/datasets/{value}/documents" if value else "/datasets", "plugins": "/plugins", "models": "/apps?settings=provider"}
    if target not in paths:
        raise DomainError(422, "invalid_native_target", "该原生页面入口不存在")
    return base + paths[target]
