"""Private, native-authenticated download of an explicitly selected saved revision."""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import DependencyUnavailable, EnterpriseError, InvalidInput
from enterprise_platform.application.office_export import OfficeExportService

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def add_office_routes(
    router: APIRouter,
    exports: OfficeExportService | None,
    actor: Callable[[Request], Awaitable[Principal]],
) -> None:
    class PrivateOfficeRoute(APIRoute):
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
                response.headers["X-Content-Type-Options"] = "nosniff"
                return response

            return private_handler

    routes = APIRouter(route_class=PrivateOfficeRoute)

    @routes.get(
        "/enterprise/api/v1/office/files/{file_id}/document",
        response_class=Response,
        responses={200: {"content": {DOCX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}}}},
    )
    def download_document(
        file_id: UUID,
        expected_revision: Annotated[str, Query(pattern=r"^[1-9][0-9]{0,18}$")],
        principal: Annotated[Principal, Depends(actor)],
    ) -> Response:
        if exports is None:
            raise DependencyUnavailable()
        result = exports.export_document(principal, file_id, expected_revision=int(expected_revision))
        return Response(
            content=result.content,
            media_type=result.media_type,
            headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
        )

    router.include_router(routes)
