"""Private native registry bridge; never expose through the browser proxy or OpenAPI."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from enterprise_platform.application.errors import EnterpriseError, InvalidInput
from enterprise_platform.application.native_registration import NativeRegistrationService


def create_registration_router(service: NativeRegistrationService) -> APIRouter:
    router = APIRouter(prefix="/enterprise/internal/v1", include_in_schema=False)
    private = {"Cache-Control": "private, no-store"}

    @router.get("/workflow-registration")
    async def registration(request: Request) -> JSONResponse:
        try:
            service.authenticate(
                request.headers.get("x-enterprise-registration-token", ""), browser_origin="origin" in request.headers
            )
            query = request.query_params
            fields = {"workspace_id", "app_id", "workflow_id"}
            if set(query) != fields or len(query.multi_items()) != 3:
                raise InvalidInput("invalid_registration_query")
            async with asyncio.timeout(10):
                result = await service.read(query["workspace_id"], query["app_id"], query["workflow_id"])
            return JSONResponse(result.model_dump(mode="json") if result is not None else None, headers=private)
        except EnterpriseError as error:
            return JSONResponse({"code": error.code}, status_code=error.status_code, headers=private)
        except TimeoutError:
            return JSONResponse({"code": "registration_timeout"}, status_code=504, headers=private)
        except Exception:
            return JSONResponse({"code": "registration_unavailable"}, status_code=503, headers=private)

    return router
