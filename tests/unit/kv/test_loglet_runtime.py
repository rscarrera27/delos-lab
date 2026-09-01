import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.native_loglet.errors import (
    EntryConflict,
    IncarnationMismatch,
    NotSequencer,
)
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer_registry import LogServerSequencerRegistry
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.native_loglet.types import AppendResult, LogEntry
from delos_lab.native_loglet.virtual_log_adapter import (
    HttpNativeLogletProvider,
    HttpSequencerTransport,
)
from delos_lab.virtual_log.types import LogSegment

MEMBERS = ("db-1", "db-2", "db-3")


def segment(sequencer: str = "db-2", incarnation: str = "inc-2") -> LogSegment:
    return LogSegment(
        segment_id="s1",
        virtual_start=0,
        virtual_stop=None,
        loglet=native_loglet_configuration(
            MEMBERS,
            sequencer,
            incarnation,
        ),
    )


class RecordingSequencerTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, LogSegment, str, bytes]] = []

    async def append(
        self, node_id: str, selected: LogSegment, command_id: str, payload: bytes
    ) -> AppendResult:
        self.calls.append((node_id, selected, command_id, payload))
        return AppendResult(position=4, known_tail=5)


class RecordingLogletTransport(DirectLogletTransport):
    def __init__(self, stores: dict[str, MemoryLogletStore]) -> None:
        super().__init__(stores)
        self.get_calls: list[str] = []

    async def get(
        self,
        node_id: str,
        segment_id: str,
        position: int,
        known_tail: int = 0,
    ) -> LogEntry | None:
        self.get_calls.append(node_id)
        return await super().get(node_id, segment_id, position, known_tail)


async def test_remote_runtime_forwards_to_configured_sequencer() -> None:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    sequencers = RecordingSequencerTransport()
    provider = HttpNativeLogletProvider("db-2", DirectLogletTransport(stores), sequencers)
    active = segment()

    result = await provider.get(active).append("c/r", b"payload")

    assert result.position == 4
    assert provider.get(active).known_tail == 5
    assert sequencers.calls == [("db-2", active, "c/r", b"payload")]


async def test_remote_runtime_observes_the_last_real_check_tail_result() -> None:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    runtime = HttpNativeLogletProvider(
        "db-2",
        DirectLogletTransport(stores),
        RecordingSequencerTransport(),
    ).get(segment())

    assert runtime.last_check_tail is None

    result = await runtime.check_tail()

    assert result.tail == 0
    assert runtime.last_check_tail == result


async def test_native_provider_observation_does_not_create_remote_runtime() -> None:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    provider = HttpNativeLogletProvider(
        "db-2",
        DirectLogletTransport(stores),
        RecordingSequencerTransport(),
    )
    active = segment()

    assert provider.peek(active) is None


def test_provider_treats_virtual_boundary_closure_as_the_same_native_loglet() -> None:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    provider = HttpNativeLogletProvider(
        "db-2",
        DirectLogletTransport(stores),
        RecordingSequencerTransport(),
    )
    active = segment()

    runtime = provider.get(active)

    assert provider.get(active.model_copy(update={"virtual_stop": 1})) is runtime


async def test_remote_runtime_reads_local_logserver_before_remote_fallback() -> None:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    transport = RecordingLogletTransport(stores)
    entry = LogEntry(segment_id="s1", position=0, command_id="r1", payload=b"x")
    await stores["db-1"].repair(entry)
    runtime = HttpNativeLogletProvider("db-2", transport, RecordingSequencerTransport()).get(
        segment()
    )
    runtime.observe_known_tail(1)

    observed = await runtime.read_next(0, 1)
    assert observed is not None
    assert (observed.command_id, observed.payload) == (entry.command_id, entry.payload)
    assert transport.get_calls == ["db-2", "db-1"]
    assert (await stores["db-2"].state("s1")).known_tail == 1


async def test_remote_runtime_returns_local_entry_without_remote_read() -> None:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    transport = RecordingLogletTransport(stores)
    entry = LogEntry(segment_id="s1", position=0, command_id="r1", payload=b"x")
    await stores["db-2"].repair(entry)
    runtime = HttpNativeLogletProvider("db-2", transport, RecordingSequencerTransport()).get(
        segment()
    )

    observed = await runtime.read_next(0, 1)
    assert observed is not None
    assert (observed.command_id, observed.payload) == (entry.command_id, entry.payload)
    assert transport.get_calls == ["db-2"]


async def test_registry_only_serves_local_current_incarnation() -> None:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    registry = LogServerSequencerRegistry("db-1", "inc-new", DirectLogletTransport(stores))

    try:
        await registry.append(segment(sequencer="db-2"), "r", b"x")
    except NotSequencer:
        pass
    else:
        raise AssertionError("expected NotSequencer")

    try:
        await registry.append(segment(sequencer="db-1", incarnation="inc-old"), "r", b"x")
    except IncarnationMismatch:
        pass
    else:
        raise AssertionError("expected IncarnationMismatch")


async def test_registry_reuses_sequencer_positions() -> None:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    registry = LogServerSequencerRegistry("db-1", "inc-1", DirectLogletTransport(stores))
    active = segment(sequencer="db-1", incarnation="inc-1")

    first = await registry.append(active, "r1", b"first")
    second = await registry.append(active, "r2", b"second")

    assert (first.position, second.position) == (0, 1)
    await registry.close()


async def test_registry_keeps_append_alive_after_request_cancellation() -> None:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    transport = DirectLogletTransport(stores)
    transport.unavailable.update(("db-2", "db-3"))
    registry = LogServerSequencerRegistry("db-1", "inc-1", transport)
    active = segment(sequencer="db-1", incarnation="inc-1")
    caller = asyncio.create_task(registry.append(active, "r1", b"first"))
    await asyncio.sleep(0.02)

    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    transport.unavailable.clear()
    result = await asyncio.wait_for(registry.append(active, "r1", b"first"), timeout=1)
    assert result.position == 0
    await registry.close()


async def test_http_transport_restores_request_conflict() -> None:
    app = FastAPI()

    @app.post("/internal/segments/s1/append")
    async def conflict() -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": "ENTRY_CONFLICT"})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://db-1"
    ) as client:
        transport = HttpSequencerTransport({"db-1": "http://db-1"}, client)
        with pytest.raises(EntryConflict):
            await transport.append("db-1", segment(sequencer="db-1"), "r", b"payload")
