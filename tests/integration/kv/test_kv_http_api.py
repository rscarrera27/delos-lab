from pathlib import Path

import httpx

from delos_lab.kv.sqlite_store import SQLiteKvStore
from delos_lab.kv.types import KvCommandEnvelope, KvResult
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer_registry import LogServerSequencerRegistry
from delos_lab.native_loglet.server import NativeLogServer
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.runtime.converged_http import create_converged_app
from delos_lab.virtual_log.loglet import LogletTail
from delos_lab.virtual_log.types import LogChain, LogSegment, VersionedLogChain

MEMBERS = ("db-1", "db-2", "db-3")


class StubService:
    def __init__(self, store: SQLiteKvStore) -> None:
        self.node_id = "db-1"
        self.store = store
        self.commands: list[KvCommandEnvelope] = []
        self.chain: VersionedLogChain | None = None

    async def submit(self, command: KvCommandEnvelope) -> KvResult:
        self.commands.append(command)
        return KvResult(code="APPLIED", value=getattr(command.operation, "value", None))

    async def get(self, key: str) -> int | None:
        del key
        return 4

    def cached_chain(self) -> VersionedLogChain | None:
        return self.chain


class StubNativeRuntime:
    known_tail = 1
    last_check_tail = LogletTail(tail=1, sealed=False)


class StubNativeObserver:
    def peek(self, _segment: LogSegment) -> StubNativeRuntime:
        return StubNativeRuntime()


async def test_public_kv_and_internal_sequencer_api(tmp_path: Path) -> None:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    kv_store = SQLiteKvStore(tmp_path / "node.sqlite")
    await kv_store.open()
    service = StubService(kv_store)
    registry = LogServerSequencerRegistry("db-1", "inc-1", DirectLogletTransport(stores))
    app = create_converged_app(
        node_id="db-1",
        incarnation_id="inc-1",
        service=service,
        loglet_observer=StubNativeObserver(),
        registry=registry,
        log_server=NativeLogServer(stores["db-1"]),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://db-1"
    ) as client:
        put = await client.put(
            "/kv/count",
            json={"client_id": "browser", "request_id": "1", "value": 4},
        )
        get = await client.get("/kv/count")
        health = await client.get("/health")
        active = LogSegment(
            segment_id="s1",
            virtual_start=0,
            virtual_stop=None,
            loglet=native_loglet_configuration(
                MEMBERS,
                "db-1",
                "inc-1",
            ),
        )
        service.chain = VersionedLogChain(
            version=1,
            chain=LogChain(segments=(active,)),
        )
        append = await client.post(
            "/internal/segments/s1/append",
            json={
                "segment": active.model_dump(mode="json"),
                "command_id": "browser/1",
                "payload": "payload",
            },
        )
        removed_watermark = await client.post(
            "/internal/segments/s1/watermark",
            json={"segment": active.model_dump(mode="json")},
        )
        state = await client.get("/state")
        preflight = await client.options(
            "/kv/count",
            headers={
                "Origin": "http://127.0.0.1:9400",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert put.json() == {"code": "APPLIED", "value": 4}
    assert get.json() == {"key": "count", "value": 4}
    assert health.json()["incarnation_id"] == "inc-1"
    assert append.json() == {"status": "committed", "position": 0, "known_tail": 1}
    assert removed_watermark.status_code == 404
    assert state.json() == {
        "node_id": "db-1",
        "process": {"status": "online", "incarnation_id": "inc-1"},
        "application": {
            "applied_position": None,
            "values": {},
            "request_count": 0,
        },
        "virtual_log": {
            "chain_version": 1,
            "cached_chain": service.chain.model_dump(mode="json"),
            "active_segment": "s1",
            "active_virtual_start": 0,
            "known_virtual_tail": 1,
        },
        "native_loglet_client": {
            "segment_id": "s1",
            "known_tail": 1,
            "last_check_tail": {"tail": 1, "sealed": False},
        },
        "sequencer": {"segment_id": "s1", "known_tail": 1},
        "log_server": {
            "segments": [
                {
                    "segment_id": "s1",
                    "local_tail": 1,
                    "trimmed_prefix": 0,
                    "known_tail": 0,
                    "sealed": False,
                }
            ]
        },
    }
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:9400"
    assert "PUT" in preflight.headers["access-control-allow-methods"]
    await registry.close()
    await kv_store.close()
