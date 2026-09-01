from typing import Annotated

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from delos_lab.virtual_log.metastore import Applied, VersionMismatch
from delos_lab.virtual_log.types import LogChain

from .acceptor import PaxosAcceptor
from .client import PaxosMetaStore
from .errors import PaxosNoQuorum
from .types import AcceptRequest, DecideRequest, PrepareRequest


class CompareAndSetHttpRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected_version: Annotated[int, Field(ge=0)]
    new_chain: LogChain


def _no_quorum() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"code": "PAXOS_NO_QUORUM"},
    )


def create_paxos_app(
    node_id: str,
    acceptor: PaxosAcceptor,
    metastore: PaxosMetaStore | None,
) -> FastAPI:
    app = FastAPI(title=f"Single-slot Paxos {node_id}")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "node_id": node_id}

    @app.get("/paxos/state")
    async def paxos_state() -> dict[str, object]:
        return {
            "node_id": node_id,
            "state": acceptor.state.model_dump(mode="json"),
        }

    @app.post("/paxos/prepare")
    async def prepare(request: PrepareRequest) -> dict[str, object]:
        response = await acceptor.prepare(request)
        return response.model_dump(mode="json")

    @app.post("/paxos/accept")
    async def accept(request: AcceptRequest) -> dict[str, object]:
        response = await acceptor.accept(request)
        return response.model_dump(mode="json")

    @app.post("/paxos/decide")
    async def decide(request: DecideRequest) -> dict[str, object]:
        response = await acceptor.decide(request)
        return response.model_dump(mode="json")

    @app.get("/metastore", response_model=None)
    async def read_metastore() -> dict[str, object] | JSONResponse:
        if metastore is None:
            return _no_quorum()
        try:
            snapshot = await metastore.read()
        except PaxosNoQuorum:
            return _no_quorum()
        return snapshot.model_dump(mode="json")

    @app.post("/metastore/compare-and-set", response_model=None)
    async def compare_and_set(
        request: Annotated[CompareAndSetHttpRequest, Body()],
    ) -> dict[str, object] | JSONResponse:
        if metastore is None:
            return _no_quorum()
        try:
            result = await metastore.compare_and_set(
                request.expected_version,
                request.new_chain,
            )
        except PaxosNoQuorum:
            return _no_quorum()
        if isinstance(result, Applied):
            return {
                "status": "applied",
                "snapshot": result.snapshot.model_dump(mode="json"),
            }
        if isinstance(result, VersionMismatch):
            return {
                "status": "version_mismatch",
                "current": result.current.model_dump(mode="json"),
            }
        raise TypeError("unknown compare-and-set result")

    return app
