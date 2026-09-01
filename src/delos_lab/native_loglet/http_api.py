from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from .errors import EntryConflict, PositionTrimmed, PredecessorUnavailable, SegmentSealed
from .server import LogServer, NativeLogServer
from .store import LogletStore
from .types import KnownTailRequest, LogEntry, LogletWriteRequest, LogServerState


def create_loglet_app(
    store: LogletStore,
    *,
    health_details: dict[str, str] | None = None,
) -> FastAPI:
    app = FastAPI(title=f"NativeLoglet {store.node_id}")
    install_loglet_api(app, NativeLogServer(store), health_details=health_details)
    return app


def install_loglet_api(
    app: FastAPI,
    server: LogServer,
    *,
    health_details: dict[str, str] | None = None,
) -> None:
    """Install NativeLoglet transport routes on a caller-owned process app."""

    @app.exception_handler(SegmentSealed)
    async def handle_sealed(_request: Request, error: SegmentSealed) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": "SEALED", "message": str(error)},
        )

    @app.exception_handler(EntryConflict)
    async def handle_conflict(_request: Request, error: EntryConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": "ENTRY_CONFLICT", "message": str(error)},
        )

    @app.exception_handler(PredecessorUnavailable)
    async def handle_predecessor(_request: Request, error: PredecessorUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": "PREDECESSOR_UNAVAILABLE", "message": str(error)},
        )

    @app.exception_handler(PositionTrimmed)
    async def handle_trimmed(_request: Request, error: PositionTrimmed) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": "TRIMMED", "message": str(error)},
        )

    def validate_path(entry: LogEntry, segment_id: str, position: int) -> None:
        if entry.segment_id != segment_id or entry.position != position:
            raise HTTPException(status_code=422, detail="entry does not match path")

    @app.put("/segments/{segment_id}/entries/{position}")
    async def put_entry(
        segment_id: str, position: int, request: LogletWriteRequest
    ) -> LogServerState:
        validate_path(request.entry, segment_id, position)
        return await server.put(request.entry, request.known_tail)

    @app.put("/segments/{segment_id}/repairs/{position}")
    async def repair_entry(
        segment_id: str, position: int, request: LogletWriteRequest
    ) -> LogServerState:
        validate_path(request.entry, segment_id, position)
        return await server.repair(request.entry, request.known_tail)

    @app.get("/segments/{segment_id}/entries/{position}")
    async def get_entry(segment_id: str, position: int, known_tail: int = 0) -> LogEntry:
        entry = await server.get(segment_id, position, known_tail)
        if entry is None:
            raise HTTPException(status_code=404, detail="entry not found")
        return entry

    @app.get("/segments/{segment_id}/entries")
    async def get_entries(
        segment_id: str,
        start: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> tuple[LogEntry, ...]:
        return await server.entries(segment_id, start=start, limit=limit)

    @app.get("/segments/{segment_id}/state")
    async def get_state(segment_id: str, known_tail: int = 0) -> LogServerState:
        return await server.state(segment_id, known_tail)

    @app.get("/segments/{segment_id}/tail-notifications/{local_tail}")
    async def wait_for_tail(
        segment_id: str,
        local_tail: Annotated[int, Path(ge=0)],
        known_tail: Annotated[int, Query(ge=0)] = 0,
    ) -> LogServerState:
        return await server.wait_for_tail(segment_id, local_tail, known_tail)

    @app.post("/segments/{segment_id}/seal")
    async def seal_segment(segment_id: str, request: KnownTailRequest) -> LogServerState:
        return await server.seal(segment_id, request.known_tail)

    @app.post("/segments/{segment_id}/prefix-trim/{trim_position}")
    async def prefix_trim_segment(
        segment_id: str,
        trim_position: Annotated[int, Path(ge=0)],
    ) -> LogServerState:
        return await server.prefix_trim(segment_id, trim_position)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "node_id": server.node_id, **(health_details or {})}
