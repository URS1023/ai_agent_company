"""Authenticated source configuration routes; saving never connects to a source.

The host factory supplies the exact native identity/CSRF/Origin dependency used by
the rest of the business API. No additional token or alternate identity path exists.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel

from enterprise_platform.application.contracts import Page, Principal
from enterprise_platform.application.errors import DependencyUnavailable
from enterprise_platform.application.source_contracts import SourceCapabilities, SourceDraft, SourceView
from enterprise_platform.application.source_ports import SourceManagement


def add_source_routes(
    router: APIRouter,
    source_service: SourceManagement | None,
    actor: Callable[[Request], Awaitable[Principal]],
    parse_revision: Callable[[str | None], int],
    error_model: type[BaseModel],
) -> None:
    prefix = "/enterprise/api/v1/sources"
    etag = {"ETag": {"description": "Quoted source revision; send as If-Match for PUT.", "schema": {"type": "string"}}}

    def service() -> SourceManagement:
        if source_service is None:
            raise DependencyUnavailable("source_management_unavailable")
        return source_service

    def no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "private, no-store"

    @router.get(f"{prefix}/capabilities")
    def capabilities(response: Response, principal: Annotated[Principal, Depends(actor)]) -> SourceCapabilities:
        no_store(response)
        if source_service is None:
            return SourceCapabilities(
                can_manage=principal.workspace_role in {"owner", "admin"},
                write_enabled=False,
                reason_code="source_management_unavailable",
            )
        return source_service.capabilities(principal)

    @router.get(prefix)
    def list_sources(
        response: Response,
        principal: Annotated[Principal, Depends(actor)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> Page[SourceView]:
        no_store(response)
        return service().list_sources(principal, offset=offset, limit=limit)

    @router.post(prefix, status_code=201, responses={201: {"headers": etag}})
    def create_source(
        payload: SourceDraft,
        response: Response,
        principal: Annotated[Principal, Depends(actor)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ) -> SourceView:
        value = service().create_source(principal, payload, request_key=idempotency_key)
        no_store(response)
        response.headers["ETag"] = f'"{value.revision}"'
        return value

    @router.get(f"{prefix}/{{source_id}}", responses={200: {"headers": etag}})
    def get_source(source_id: str, response: Response, principal: Annotated[Principal, Depends(actor)]) -> SourceView:
        value = service().get_source(principal, source_id)
        no_store(response)
        response.headers["ETag"] = f'"{value.revision}"'
        return value

    @router.put(
        f"{prefix}/{{source_id}}",
        responses={200: {"headers": etag}, 428: {"model": error_model, "description": "If-Match required."}},
    )
    def update_source(
        source_id: str,
        payload: SourceDraft,
        response: Response,
        principal: Annotated[Principal, Depends(actor)],
        if_match: Annotated[str | None, Header()] = None,
    ) -> SourceView:
        revision = parse_revision(if_match)
        value = service().update_source(principal, source_id, payload, expected_revision=revision)
        no_store(response)
        response.headers["ETag"] = f'"{value.revision}"'
        return value
