from typing import Annotated, Protocol

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from delos_lab.kv.http_api import KvApplication, configure_kv_http, install_kv_api
from delos_lab.kv.snapshot import KvSnapshot
from delos_lab.kv.sqlite_store import SQLiteKvStore
from delos_lab.native_loglet.errors import (
    IncarnationMismatch,
    NoQuorum,
    NotSequencer,
    SequencerUnavailable,
    TailUnavailable,
)
from delos_lab.native_loglet.http_api import install_loglet_api
from delos_lab.native_loglet.membership import NativeLogletStorageMembership
from delos_lab.native_loglet.sequencer_registry import LogServerSequencerRegistry
from delos_lab.native_loglet.server import LogServer
from delos_lab.native_loglet.virtual_log_adapter import SequencerAppendRequest
from delos_lab.virtual_log.loglet import LogletTail
from delos_lab.virtual_log.types import LogSegment, VersionedLogChain


class ConvergedApplication(KvApplication, Protocol):
    store: SQLiteKvStore

    def cached_chain(self) -> VersionedLogChain | None: ...

    async def export_bootstrap_snapshot(self) -> KvSnapshot: ...


class ObservedNativeLoglet(Protocol):
    @property
    def known_tail(self) -> int: ...

    @property
    def last_check_tail(self) -> LogletTail | None: ...


class NativeLogletObserver(Protocol):
    def peek(self, segment: LogSegment) -> ObservedNativeLoglet | None: ...


def create_converged_app(
    *,
    node_id: str,
    incarnation_id: str,
    service: ConvergedApplication,
    loglet_observer: NativeLogletObserver,
    registry: LogServerSequencerRegistry,
    log_server: LogServer,
    storage_membership: NativeLogletStorageMembership | None = None,
) -> FastAPI:
    """Compose application, VirtualLog client, and NativeLoglet in one process.

    This is the only HTTP module that knows the deliberately converged failure
    unit. The KV and NativeLoglet packages remain independently reusable.
    """
    app = FastAPI(title=f"Converged Delos DB {node_id}")
    configure_kv_http(app)
    install_kv_api(app, service)
    install_loglet_api(
        app,
        log_server,
        health_details={"incarnation_id": incarnation_id},
    )

    @app.exception_handler(NotSequencer)
    async def not_sequencer(_request: Request, error: NotSequencer) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": "NOT_SEQUENCER", "message": str(error)},
        )

    @app.exception_handler(IncarnationMismatch)
    async def incarnation_mismatch(_request: Request, error: IncarnationMismatch) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": "INCARNATION_MISMATCH", "message": str(error)},
        )

    @app.exception_handler(SequencerUnavailable)
    @app.exception_handler(NoQuorum)
    @app.exception_handler(TailUnavailable)
    async def native_unavailable(_request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"code": "UNAVAILABLE", "message": str(error)},
        )

    @app.get("/state")
    async def state() -> dict[str, object]:
        cached_chain = service.cached_chain()
        active = (
            None
            if cached_chain is None or cached_chain.chain is None
            else cached_chain.chain.active
        )
        server_segments = await log_server.segment_states()
        sequencer_runtime = None if active is None else await registry.observe(active)
        client_runtime = None if active is None else loglet_observer.peek(active)
        client_known_tail = None if client_runtime is None else client_runtime.known_tail
        last_check_tail = None if client_runtime is None else client_runtime.last_check_tail
        known_virtual_tail = (
            None
            if active is None or client_known_tail is None
            else active.virtual_start + client_known_tail
        )
        applied_position = await service.store.applied_position()
        return {
            "node_id": node_id,
            "process": {"status": "online", "incarnation_id": incarnation_id},
            "application": {
                "applied_position": None if applied_position < 0 else applied_position,
                "values": await service.store.snapshot(),
                "request_count": await service.store.request_count(),
            },
            "virtual_log": {
                "chain_version": None if cached_chain is None else cached_chain.version,
                "cached_chain": (
                    None if cached_chain is None else cached_chain.model_dump(mode="json")
                ),
                "active_segment": None if active is None else active.segment_id,
                "active_virtual_start": None if active is None else active.virtual_start,
                "known_virtual_tail": known_virtual_tail,
            },
            "native_loglet_client": (
                None
                if active is None or client_known_tail is None
                else {
                    "segment_id": active.segment_id,
                    "known_tail": client_known_tail,
                    "last_check_tail": (
                        None
                        if last_check_tail is None
                        else {
                            "tail": last_check_tail.tail,
                            "sealed": last_check_tail.sealed,
                        }
                    ),
                }
            ),
            "sequencer": (
                None
                if sequencer_runtime is None
                else {
                    "segment_id": sequencer_runtime.segment_id,
                    "known_tail": sequencer_runtime.known_tail,
                }
            ),
            "log_server": {
                "segments": [segment.model_dump(mode="json") for segment in server_segments]
            },
        }

    @app.get("/internal/database/snapshot")
    async def database_snapshot() -> dict[str, object]:
        snapshot = await service.export_bootstrap_snapshot()
        return snapshot.model_dump(mode="json")

    if storage_membership is not None:

        @app.post("/internal/native-loglet/storage-membership/join")
        async def join_storage_membership() -> dict[str, object]:
            snapshot = await storage_membership.join()
            return snapshot.model_dump(mode="json")

    @app.post("/internal/segments/{segment_id}/append", response_model=None)
    async def append_internal(
        segment_id: str,
        request: Annotated[SequencerAppendRequest, Body()],
    ) -> dict[str, object] | JSONResponse:
        if request.segment.segment_id != segment_id:
            return JSONResponse(status_code=422, content={"code": "SEGMENT_MISMATCH"})
        result = await registry.append(request.segment, request.command_id, request.payload)
        return result.model_dump(mode="json")

    return app
