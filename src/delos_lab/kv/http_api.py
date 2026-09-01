from typing import Annotated, Protocol

from fastapi import Body, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt

from .errors import ReconfigurationUnavailable, SyncRequired
from .sqlite_store import RequestConflict
from .types import CompareAndSet, Delete, Increment, KvCommandEnvelope, KvResult, KvValue, Put


class KvApplication(Protocol):
    async def submit(self, command: KvCommandEnvelope) -> KvResult: ...

    async def get(self, key: str) -> KvValue | None: ...


class RequestIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)
    client_id: str
    request_id: str


class PutRequest(RequestIdentity):
    value: KvValue


class CompareAndSetRequest(RequestIdentity):
    expected: KvValue | None
    value: KvValue


class IncrementRequest(RequestIdentity):
    delta: StrictInt


def create_kv_app(
    *,
    node_id: str,
    service: KvApplication,
) -> FastAPI:
    app = FastAPI(title=f"Delos KV {node_id}")
    configure_kv_http(app)
    install_kv_api(app, service)
    return app


def configure_kv_http(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(?:127\.0\.0\.1|localhost):\d+$",
        allow_methods=["GET", "PUT", "POST", "DELETE", "OPTIONS"],
        allow_headers=["content-type"],
    )


def install_kv_api(app: FastAPI, service: KvApplication) -> None:
    """Install only the application-facing KV API on a caller-owned app."""

    @app.exception_handler(RequestConflict)
    async def request_conflict(_request: Request, error: RequestConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"code": "REQUEST_CONFLICT", "message": str(error)}
        )

    @app.exception_handler(ReconfigurationUnavailable)
    @app.exception_handler(SyncRequired)
    async def unavailable(_request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=503, content={"code": "UNAVAILABLE", "message": str(error)})

    @app.put("/kv/{key}")
    async def put(key: str, request: Annotated[PutRequest, Body()]) -> KvResult:
        return await service.submit(
            KvCommandEnvelope(
                client_id=request.client_id,
                request_id=request.request_id,
                operation=Put(key=key, value=request.value),
            )
        )

    @app.delete("/kv/{key}")
    async def delete(key: str, request: Annotated[RequestIdentity, Body()]) -> KvResult:
        return await service.submit(
            KvCommandEnvelope(
                client_id=request.client_id,
                request_id=request.request_id,
                operation=Delete(key=key),
            )
        )

    @app.post("/kv/{key}/compare-and-set")
    async def compare_and_set(
        key: str, request: Annotated[CompareAndSetRequest, Body()]
    ) -> KvResult:
        return await service.submit(
            KvCommandEnvelope(
                client_id=request.client_id,
                request_id=request.request_id,
                operation=CompareAndSet(
                    key=key,
                    expected=request.expected,
                    value=request.value,
                ),
            )
        )

    @app.post("/kv/{key}/increment")
    async def increment(key: str, request: Annotated[IncrementRequest, Body()]) -> KvResult:
        return await service.submit(
            KvCommandEnvelope(
                client_id=request.client_id,
                request_id=request.request_id,
                operation=Increment(key=key, delta=request.delta),
            )
        )

    @app.get("/kv/{key}")
    async def get(key: str) -> dict[str, KvValue | None]:
        return {"key": key, "value": await service.get(key)}
