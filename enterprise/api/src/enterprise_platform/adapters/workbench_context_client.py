"""Bounded native workbench checks and chat transport using an ephemeral session.

This client does not implement MessageSendAuthority. Successful context checks do
not authorize branches or generation. Attachment checks send selected file mappings;
input checks send workflow input values. Only open_chat submits the generation payload.
No refresh tokens or caller-selected
endpoint URLs cross this boundary, and no POST retries
or redirect following occur. Each invocation has an isolated HTTP client.
"""

import asyncio
import math
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from enterprise_platform.application.contracts import JsonObject, Principal
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable, EnterpriseError, InvalidState
from enterprise_platform.application.workbench_branch_listing import BranchListQuery
from enterprise_platform.application.workbench_messages import ChatSendIntent, MessageScope, NativeMessageReceipt
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .dify_identity import _forwarded_headers
from .workbench_stream import ChatStreamInvalid, read_chat_events
from .workbench_stream_identity import ChatStreamIdentity

_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_FILES: TypeAdapter[list[JsonObject]] = TypeAdapter(list[JsonObject])


class ContextCheckRejected(DependencyUnavailable):
    code = "native_chat_context_unavailable"


class ChatDispatchUncertain(DependencyUnavailable):
    code = "native_chat_dispatch_uncertain"


class _ContextResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    workspace_id: str
    actor_id: str
    installed_app_id: str
    context_verified: bool
    attachments_verified: bool
    branch_verified: bool


class _AttachmentResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    workspace_id: str
    actor_id: str
    installed_app_id: str
    file_count: int
    selected_files_verified: bool
    input_files_verified: bool
    branch_verified: bool


class _InputResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    workspace_id: str
    actor_id: str
    installed_app_id: str
    inputs_verified: bool
    selected_files_verified: bool
    branch_verified: bool


class PlainChatTerminalObservation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)

    version: int = Field(ge=1, le=1)
    task_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    outcome: Literal["succeeded", "stopped", "failed"]
    stop_reason: Literal["user_manual", "annotation_reply", "output_moderation", "input_moderation"] | None

    @model_validator(mode="after")
    def consistent_stop(self) -> Self:
        if (self.outcome == "stopped") != (self.stop_reason is not None):
            raise ValueError("Inconsistent native stop evidence")
        return self


class GenerationStateObservation(BaseModel):
    """Native status metadata, not proof of task completion or a branch release permit."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    workspace_id: str
    actor_id: str
    installed_app_id: str
    conversation_id: str
    message_id: str
    message_status: Literal["normal", "paused", "error"]
    workflow_run_id: str | None
    workflow_status: (
        Literal["scheduled", "running", "paused", "succeeded", "partial-succeeded", "failed", "stopped"] | None
    )
    workflow_finished: bool
    message_terminal: PlainChatTerminalObservation | None = None

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if self.message_terminal is not None:
            required_status = "error" if self.message_terminal.outcome == "failed" else "normal"
            if self.workflow_run_id is not None or self.message_status != required_status:
                raise ValueError("Plain terminal metadata requires matching workflow-free message status")
        if (self.workflow_run_id is None) != (self.workflow_status is None):
            raise ValueError("Workflow identity and status must be paired")
        if self.workflow_run_id is not None:
            run_id = UUID(self.workflow_run_id)
            if not run_id.int or str(run_id) != self.workflow_run_id:
                raise ValueError("Canonical nonzero workflow identity required")
        if self.workflow_finished and (
            self.message_status == "paused"
            or self.workflow_status not in {"succeeded", "partial-succeeded", "failed", "stopped"}
        ):
            raise ValueError("Inconsistent workflow completion metadata")
        return self


class DifyWorkbenchContextClient:
    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 65536,
    ) -> None:
        try:
            parsed = urlsplit(base_url)
            normalized = httpx.URL(base_url)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.port != 0
                and not any((parsed.username, parsed.password, parsed.query, parsed.fragment))
                and parsed.path.rstrip("/").endswith("/console/api")
                and not any(c.isspace() for c in base_url)
                and not any(segment in {".", ".."} for segment in parsed.path.split("/"))
            )
        except (ValueError, httpx.InvalidURL):
            valid = False
        if not valid:
            raise ValueError("A fixed native console API endpoint is required")
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or type(max_response_bytes) is not int
            or not 1 <= max_response_bytes <= 2 * 1024 * 1024
        ):
            raise ValueError("Bounded response size and positive deadline required")
        self._base_url = str(normalized).rstrip("/") + "/"
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def _body(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_response_bytes:
                raise ContextCheckRejected()
            chunks.append(chunk)
        return b"".join(chunks)

    @asynccontextmanager
    async def open_chat(
        self, principal: Principal, session: NativeSetupSession, claimed: ChatSendIntent
    ) -> AsyncIterator[AsyncIterator[JsonObject]]:
        """Submit once per invocation, only after a durable claim from the send service.

        Caller owns once-only dispatch coordination and uncertain/receipt persistence.
        This internal transport does not prove claim provenance, retry, release branches
        or infer completion from EOF. Consume inside the context to close the HTTP stream
        on errors, cancellation or early exit. The deadline covers the entire context.
        """
        try:
            claimed = ChatSendIntent.model_validate_json(claimed.model_dump_json())
        except (ValueError, TypeError, RecursionError):
            raise InvalidState("invalid_chat_dispatch_claim") from None
        if claimed.status != "dispatched":
            raise InvalidState("chat_dispatch_requires_claim")
        scope = claimed.scope
        if (scope.workspace_id, scope.actor_id) != (principal.workspace_id, principal.actor_id):
            raise AccessDenied()
        identity = ChatStreamIdentity(claimed)
        headers = _forwarded_headers(session.cookie_header, session.authorization, session.csrf_token)
        headers.update(
            {
                "X-Enterprise-Expected-Workspace": scope.workspace_id,
                "X-Enterprise-Expected-Actor": scope.actor_id,
                "Accept": "text/event-stream",
                "Accept-Encoding": "identity",
                "Content-Type": "application/json",
            }
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=headers,
                    transport=self._transport,
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        f"installed-apps/{scope.installed_app_id}/enterprise/workbench/chat-messages",
                        content=claimed.payload_json.encode("utf-8"),
                    ) as response:
                        if (
                            response.status_code != 200
                            or response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                            != "text/event-stream"
                            or response.headers.get("content-encoding", "identity").lower() != "identity"
                        ):
                            raise ChatDispatchUncertain()
                        events = read_chat_events(response.aiter_bytes(), max_frame_bytes=self._max_response_bytes)

                        async def checked_events() -> AsyncGenerator[JsonObject, None]:
                            async for event in events:
                                identity.observe(event)
                                yield event

                        checked = checked_events()
                        try:
                            yield checked
                        finally:
                            await checked.aclose()
                            await events.aclose()
        except (httpx.HTTPError, TimeoutError, ChatStreamInvalid):
            raise ChatDispatchUncertain() from None

    async def check_context(self, principal: Principal, session: NativeSetupSession, requested: ChatSendIntent) -> None:
        """Check current context only; return no reusable or full-send permit."""
        try:
            requested = ChatSendIntent.model_validate_json(requested.model_dump_json())
            scope = requested.scope
            if (scope.workspace_id, scope.actor_id) != (principal.workspace_id, principal.actor_id):
                raise ContextCheckRejected()
            payload = _OBJECT.validate_json(requested.payload_json)
            context: dict[str, str | None] = {}
            for key in ("conversation_id", "parent_message_id"):
                if key not in payload:
                    continue
                value = payload[key]
                if value is not None and not isinstance(value, str):
                    raise ContextCheckRejected()
                if value:
                    UUID(value)
                context[key] = value
            await self._check_native_context(principal, session, scope, context)
        except ContextCheckRejected:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, EnterpriseError, RecursionError):
            raise ContextCheckRejected() from None

    async def check_branch_access(self, principal: Principal, session: NativeSetupSession, scope: MessageScope) -> None:
        """Verify current native app access without creating or pretending to send a message."""
        await self._check_native_context(principal, session, scope, {})

    async def check_listing_access(
        self, principal: Principal, session: NativeSetupSession, query: BranchListQuery
    ) -> None:
        """Check native app access without manufacturing a branch or forwarding pagination."""
        await self._check_native_context(principal, session, query, {})

    async def _check_native_context(
        self,
        principal: Principal,
        session: NativeSetupSession,
        scope: MessageScope | BranchListQuery,
        context: dict[str, str | None],
    ) -> None:
        try:
            scope = (
                BranchListQuery.model_validate_json(scope.model_dump_json())
                if isinstance(scope, BranchListQuery)
                else MessageScope.model_validate_json(scope.model_dump_json())
            )
            if (scope.workspace_id, scope.actor_id) != (principal.workspace_id, principal.actor_id):
                raise ContextCheckRejected()
            headers = _forwarded_headers(session.cookie_header, session.authorization, session.csrf_token)
            headers.update(
                {
                    "X-Enterprise-Expected-Workspace": scope.workspace_id,
                    "X-Enterprise-Expected-Actor": scope.actor_id,
                }
            )
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=headers,
                    transport=self._transport,
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        f"installed-apps/{scope.installed_app_id}/enterprise/workbench/context-check",
                        json=context,
                    ) as response:
                        if response.status_code != 200:
                            raise ContextCheckRejected()
                        result = _ContextResponse.model_validate_json(await self._body(response))
                        if (
                            result.workspace_id != scope.workspace_id
                            or result.actor_id != scope.actor_id
                            or result.installed_app_id != str(scope.installed_app_id)
                            or not result.context_verified
                            or result.attachments_verified
                            or result.branch_verified
                        ):
                            raise ContextCheckRejected()
        except ContextCheckRejected:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, EnterpriseError, RecursionError):
            raise ContextCheckRejected() from None

    async def read_generation_state(
        self, principal: Principal, session: NativeSetupSession, receipt: NativeMessageReceipt
    ) -> GenerationStateObservation:
        """Read scoped durable state without inferring stream/task completion."""
        try:
            receipt = NativeMessageReceipt.model_validate_json(receipt.model_dump_json())
            scope = receipt.scope
            if (scope.workspace_id, scope.actor_id) != (principal.workspace_id, principal.actor_id):
                raise ContextCheckRejected()
            headers = _forwarded_headers(session.cookie_header, session.authorization, session.csrf_token)
            headers.update(
                {"X-Enterprise-Expected-Workspace": scope.workspace_id, "X-Enterprise-Expected-Actor": scope.actor_id}
            )
            expected = {
                "workspace_id": scope.workspace_id,
                "actor_id": scope.actor_id,
                "installed_app_id": str(scope.installed_app_id),
                "conversation_id": str(receipt.conversation_id),
                "message_id": str(receipt.message_id),
            }
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=headers,
                    transport=self._transport,
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        f"installed-apps/{scope.installed_app_id}/enterprise/workbench/generation-state",
                        json={"conversation_id": expected["conversation_id"], "message_id": expected["message_id"]},
                    ) as response:
                        if response.status_code != 200:
                            raise ContextCheckRejected()
                        result = GenerationStateObservation.model_validate_json(await self._body(response))
                        if any(getattr(result, key) != value for key, value in expected.items()):
                            raise ContextCheckRejected()
                        if result.message_terminal is not None and result.message_terminal.task_id != receipt.task_id:
                            raise ContextCheckRejected()
                        return result
        except ContextCheckRejected:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, EnterpriseError, RecursionError):
            raise ContextCheckRejected() from None

    async def check_selected_attachments(
        self, principal: Principal, session: NativeSetupSession, requested: ChatSendIntent
    ) -> None:
        """Check this selection only; file-valued inputs and branch authority remain separate."""
        try:
            requested = ChatSendIntent.model_validate_json(requested.model_dump_json())
            scope = requested.scope
            if (scope.workspace_id, scope.actor_id) != (principal.workspace_id, principal.actor_id):
                raise ContextCheckRejected()
            payload = _OBJECT.validate_json(requested.payload_json)
            selected = payload.get("files")
            files = _FILES.validate_python([] if selected is None else selected, strict=True)
            outgoing: JsonObject = {"files": [item for item in files]}
            for key in ("conversation_id", "parent_message_id"):
                if key not in payload:
                    continue
                value = payload[key]
                if value is not None and not isinstance(value, str):
                    raise ContextCheckRejected()
                if value:
                    UUID(value)
                outgoing[key] = value
            headers = _forwarded_headers(session.cookie_header, session.authorization, session.csrf_token)
            headers.update(
                {"X-Enterprise-Expected-Workspace": scope.workspace_id, "X-Enterprise-Expected-Actor": scope.actor_id}
            )
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=headers,
                    transport=self._transport,
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        f"installed-apps/{scope.installed_app_id}/enterprise/workbench/attachment-check",
                        json=outgoing,
                    ) as response:
                        if response.status_code != 200:
                            raise ContextCheckRejected()
                        result = _AttachmentResponse.model_validate_json(await self._body(response))
                        if (
                            result.workspace_id != scope.workspace_id
                            or result.actor_id != scope.actor_id
                            or result.installed_app_id != str(scope.installed_app_id)
                            or result.file_count != len(files)
                            or not result.selected_files_verified
                            or result.input_files_verified
                            or result.branch_verified
                        ):
                            raise ContextCheckRejected()
        except ContextCheckRejected:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, EnterpriseError, RecursionError):
            raise ContextCheckRejected() from None

    async def check_inputs(self, principal: Principal, session: NativeSetupSession, requested: ChatSendIntent) -> None:
        """Check native form inputs; selected attachments and branch authority remain separate."""
        try:
            requested = ChatSendIntent.model_validate_json(requested.model_dump_json())
            scope = requested.scope
            if (scope.workspace_id, scope.actor_id) != (principal.workspace_id, principal.actor_id):
                raise ContextCheckRejected()
            payload = _OBJECT.validate_json(requested.payload_json)
            inputs = _OBJECT.validate_python(payload.get("inputs"), strict=True)
            outgoing: JsonObject = {"inputs": inputs}
            for key in ("conversation_id", "parent_message_id"):
                if key not in payload:
                    continue
                value = payload[key]
                if value is not None and not isinstance(value, str):
                    raise ContextCheckRejected()
                if value:
                    UUID(value)
                outgoing[key] = value
            headers = _forwarded_headers(session.cookie_header, session.authorization, session.csrf_token)
            headers.update(
                {"X-Enterprise-Expected-Workspace": scope.workspace_id, "X-Enterprise-Expected-Actor": scope.actor_id}
            )
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers=headers,
                    transport=self._transport,
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        f"installed-apps/{scope.installed_app_id}/enterprise/workbench/input-check",
                        json=outgoing,
                    ) as response:
                        if response.status_code != 200:
                            raise ContextCheckRejected()
                        result = _InputResponse.model_validate_json(await self._body(response))
                        if (
                            result.workspace_id != scope.workspace_id
                            or result.actor_id != scope.actor_id
                            or result.installed_app_id != str(scope.installed_app_id)
                            or not result.inputs_verified
                            or result.selected_files_verified
                            or result.branch_verified
                        ):
                            raise ContextCheckRejected()
        except ContextCheckRejected:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, EnterpriseError, RecursionError):
            raise ContextCheckRejected() from None
