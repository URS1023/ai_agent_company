"""Private native-authenticated workbench streaming and read-only state endpoints.

Identity/envelope errors precede headers. Once SSE starts, send/preflight/runtime errors
are explicit enterprise_send_error events, never a fabricated generation completion.
The dispatcher context lives inside the streaming task so cancellation closes native I/O.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from typing import Annotated
from uuid import UUID

from anyio import CancelScope
from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, field_validator
from starlette.types import Send

from enterprise_platform.adapters.workbench_dispatch import WorkbenchChatDispatcher
from enterprise_platform.application.contracts import Contract, Identifier, JsonObject, Principal, canonical_json
from enterprise_platform.application.errors import DependencyUnavailable, EnterpriseError, InvalidInput
from enterprise_platform.application.workbench_branch_listing import BranchPage
from enterprise_platform.application.workbench_branch_service import WorkbenchBranchService
from enterprise_platform.application.workbench_branches import BranchContext
from enterprise_platform.application.workbench_messages import ChatSendIntent, GenerationOutcome, SendStatus
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession


class RootBranchRequest(Contract):
    branch_id: Identifier


class WorkbenchSendState(Contract):
    """Public ledger observation, shared by GET and SSE; excludes prompts and credentials."""

    client_message_id: UUID
    status: SendStatus
    revision: int = Field(ge=1)
    conversation_id: UUID | None
    message_id: UUID | None
    task_id: Identifier | None
    outcome: GenerationOutcome | None


def _send_state(intent: ChatSendIntent) -> WorkbenchSendState:
    receipt = intent.receipt
    return WorkbenchSendState(
        client_message_id=intent.client_message_id,
        status=intent.status,
        revision=intent.revision,
        conversation_id=receipt.conversation_id if receipt else None,
        message_id=receipt.message_id if receipt else None,
        task_id=receipt.task_id if receipt else None,
        outcome=intent.terminal.outcome if intent.terminal else None,
    )


class ChatSendRequest(Contract):
    client_message_id: UUID
    payload: JsonObject

    @field_validator("client_message_id")
    @classmethod
    def nonzero_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("Message identifier required")
        return value


class ChatStreamingResponse(StreamingResponse):
    def __init__(self, events: AsyncGenerator[bytes, None]) -> None:
        self._events = events
        super().__init__(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
        )

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            # Send failures do not automatically close a suspended async generator.
            with CancelScope(shield=True):
                await self._events.aclose()


def _frame(value: JsonObject) -> bytes:
    return ("data: " + canonical_json(value) + "\n\n").encode("utf-8")


def _state(intent: ChatSendIntent) -> bytes:
    return _frame(
        {
            "event": "enterprise_send_state",
            "data": _send_state(intent).model_dump(mode="json"),
        }
    )


def add_workbench_routes(
    router: APIRouter,
    workbench: WorkbenchChatDispatcher | None,
    actor: Callable[[Request], Awaitable[Principal]],
    error_model: type[BaseModel],
    branches: WorkbenchBranchService | None = None,
) -> None:
    class PrivateChatRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[None, None, Response]]:
            handler = super().get_route_handler()

            async def private_handler(request: Request) -> Response:
                try:
                    response = await handler(request)
                except EnterpriseError as error:
                    response = JSONResponse(status_code=error.status_code, content={"code": error.code})
                except RequestValidationError:
                    response = JSONResponse(status_code=422, content={"code": InvalidInput.code})
                response.headers["Cache-Control"] = "private, no-store"
                return response

            return private_handler

    routes = APIRouter(
        route_class=PrivateChatRoute,
        responses={code: {"model": error_model} for code in (401, 403, 409, 422, 503)},
    )

    @routes.post(
        "/enterprise/api/v1/workbench/apps/{installed_app_id}/branches/{branch_id}/messages",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "Native events with enterprise_send_state and enterprise_send_error envelopes.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
    )
    async def send_message(
        request: Request,
        body: ChatSendRequest,
        installed_app_id: UUID,
        branch_id: Annotated[str, Path(min_length=1, max_length=128)],
        principal: Annotated[Principal, Depends(actor)],
    ) -> StreamingResponse:
        if workbench is None:
            raise DependencyUnavailable("workbench_unavailable")
        active = workbench
        session = NativeSetupSession(
            cookie_header=request.headers.get("cookie"),
            authorization=request.headers.get("authorization"),
            csrf_token=request.headers.get("x-csrf-token"),
        )

        async def stream_body() -> AsyncGenerator[bytes, None]:
            try:
                async with active.open(
                    principal,
                    session,
                    installed_app_id=installed_app_id,
                    branch_id=branch_id,
                    client_message_id=body.client_message_id,
                    payload=body.payload,
                ) as stream:
                    yield _state(stream.intent)
                    async for event in stream.events:
                        yield _frame(event)
                yield _state(stream.intent)
            except EnterpriseError as error:
                yield _frame({"event": "enterprise_send_error", "code": error.code})
            except Exception:
                yield _frame({"event": "enterprise_send_error", "code": EnterpriseError.code})

        return ChatStreamingResponse(stream_body())

    def branch_service() -> WorkbenchBranchService:
        if branches is None:
            raise DependencyUnavailable("workbench_branches_unavailable")
        return branches

    @routes.get(
        "/enterprise/api/v1/workbench/apps/{installed_app_id}/branches/{branch_id}/messages/{client_message_id}"
    )
    async def get_message(
        request: Request,
        installed_app_id: UUID,
        branch_id: Annotated[str, Path(min_length=1, max_length=128)],
        client_message_id: UUID,
        principal: Annotated[Principal, Depends(actor)],
    ) -> WorkbenchSendState:
        if workbench is None:
            raise DependencyUnavailable("workbench_unavailable")
        stored = await workbench.read_message(
            principal,
            branch_session(request),
            installed_app_id=installed_app_id,
            branch_id=branch_id,
            client_message_id=client_message_id,
        )
        return _send_state(stored)

    def branch_session(request: Request) -> NativeSetupSession:
        return NativeSetupSession(
            cookie_header=request.headers.get("cookie"),
            authorization=request.headers.get("authorization"),
            csrf_token=request.headers.get("x-csrf-token"),
        )

    @routes.get("/enterprise/api/v1/workbench/apps/{installed_app_id}/branches")
    async def list_branches(
        request: Request,
        installed_app_id: UUID,
        principal: Annotated[Principal, Depends(actor)],
        after: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> BranchPage:
        return await branch_service().list_branches(
            principal,
            branch_session(request),
            installed_app_id=installed_app_id,
            after=after,
            limit=limit,
        )

    @routes.post("/enterprise/api/v1/workbench/apps/{installed_app_id}/branches")
    async def create_root(
        request: Request,
        body: RootBranchRequest,
        installed_app_id: UUID,
        principal: Annotated[Principal, Depends(actor)],
    ) -> BranchContext:
        return await branch_service().create_root(
            principal,
            branch_session(request),
            installed_app_id=installed_app_id,
            branch_id=body.branch_id,
        )

    @routes.get("/enterprise/api/v1/workbench/apps/{installed_app_id}/branches/{branch_id}")
    async def get_branch(
        request: Request,
        installed_app_id: UUID,
        branch_id: Annotated[str, Path(min_length=1, max_length=128)],
        principal: Annotated[Principal, Depends(actor)],
    ) -> BranchContext:
        return await branch_service().get(
            principal,
            branch_session(request),
            installed_app_id=installed_app_id,
            branch_id=branch_id,
        )

    router.include_router(routes)
