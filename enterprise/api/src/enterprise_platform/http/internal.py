"""Private managed-plugin boundary, deliberately excluded from browser OpenAPI.

Mount only with explicitly configured node keys and specifications. Deployments
must keep this route off the browser proxy. The signature covers the exact bounded
request bytes; neither ordinary Dify sessions nor arbitrary workflow inputs grant
access. Association-pending is the sole retryable pre-read handshake response.
"""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from enterprise_platform.application.errors import EnterpriseError
from enterprise_platform.application.managed_execution import ManagedExecutionService


def create_managed_router(service: ManagedExecutionService) -> APIRouter:
    router = APIRouter(prefix="/enterprise/internal/v1", include_in_schema=False)
    private = {"Cache-Control": "private, no-store"}

    @router.post("/evaluate")
    async def evaluate(request: Request) -> JSONResponse:
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return JSONResponse({"code": "managed_json_required"}, status_code=415, headers=private)
        try:
            async with asyncio.timeout(60):
                content = bytearray()
                async for chunk in request.stream():
                    if len(content) + len(chunk) > 8192:
                        return JSONResponse({"code": "managed_request_too_large"}, status_code=413, headers=private)
                    content.extend(chunk)
                result = await service.evaluate(
                    bytes(content),
                    request.headers.get("x-enterprise-key-id", ""),
                    request.headers.get("x-enterprise-signature", ""),
                )
            return JSONResponse(result.model_dump(mode="json"), headers=private)
        except EnterpriseError as error:
            return JSONResponse({"code": error.code}, status_code=error.status_code, headers=private)
        except TimeoutError:
            return JSONResponse({"code": "managed_execution_timeout"}, status_code=504, headers=private)
        except Exception:
            return JSONResponse({"code": "managed_execution_failed"}, status_code=503, headers=private)

    return router
