import pytest

from delos_lab.metastore.memory import MemoryMetaStore
from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.native_loglet.types import LogEntry
from delos_lab.native_loglet.virtual_log_adapter import NativeLogletRuntime
from delos_lab.virtual_log.core import PositionUnavailable, VirtualLog
from delos_lab.virtual_log.loglet import StaticLogletProvider
from delos_lab.virtual_log.types import (
    LogChain,
    LogSegment,
    NewLogletConfiguration,
    VirtualLogEntry,
)


def segment(segment_id: str, start: int, end: int | None) -> LogSegment:
    return LogSegment(
        segment_id=segment_id,
        virtual_start=start,
        virtual_stop=end,
        loglet=native_loglet_configuration(
            ("db-1", "db-2", "db-3"),
            "db-1",
            f"inc-{segment_id}",
        ),
    )


def runtime(segment_id: str) -> tuple[NativeLogletRuntime, DirectLogletTransport]:
    members = ("db-1", "db-2", "db-3")
    stores = {member: MemoryLogletStore(member) for member in members}
    transport = DirectLogletTransport(stores)
    sequencer = NativeSequencer(segment_id, "db-1", members, transport)
    client = NativeLogletClient(segment_id, members, transport)
    return NativeLogletRuntime(sequencer, client, transport, members), transport


async def configured_virtual_log() -> tuple[
    VirtualLog,
    DirectLogletTransport,
]:
    first, first_transport = runtime("s1")
    second, _ = runtime("s2")
    await first.append("request-1", b"first")
    await second.append("request-2", b"second")

    chain = LogChain(segments=(segment("s1", 0, 1), segment("s2", 1, None)))
    meta_store = MemoryMetaStore()
    await meta_store.compare_and_set(0, chain)
    virtual_log = VirtualLog(
        meta_store,
        StaticLogletProvider({"s1": first, "s2": second}),
    )
    await virtual_log.refresh()
    return virtual_log, first_transport


async def test_read_routes_positions_across_segments() -> None:
    virtual_log, _ = await configured_virtual_log()

    first = await virtual_log.read(0)
    second = await virtual_log.read(1)

    assert first == VirtualLogEntry(
        position=0,
        command_id="request-1",
        payload=b"first",
        segment_id="s1",
        local_position=0,
    )
    assert second == VirtualLogEntry(
        position=1,
        command_id="request-2",
        payload=b"second",
        segment_id="s2",
        local_position=0,
    )


async def test_read_uses_another_logserver_when_first_member_is_unavailable() -> None:
    virtual_log, first_transport = await configured_virtual_log()
    first_transport.unavailable.add("db-1")

    entry = await virtual_log.read(0)

    assert entry.payload == b"first"


async def test_read_rejects_position_without_an_entry() -> None:
    virtual_log, _ = await configured_virtual_log()

    with pytest.raises(PositionUnavailable):
        await virtual_log.read(2)

    with pytest.raises(PositionUnavailable):
        await virtual_log.read(-1)


async def test_late_zombie_in_closed_loglet_stays_outside_virtual_boundary() -> None:
    members = ("db-1", "db-2", "db-3")
    stores = {member: MemoryLogletStore(member) for member in members}
    transport = DirectLogletTransport(stores)
    first_sequencer = NativeSequencer("s1", "db-1", members, transport)
    first_client = NativeLogletClient("s1", members, transport)
    first = NativeLogletRuntime(first_sequencer, first_client, transport, members)
    second_sequencer = NativeSequencer("s2", "db-2", members, transport)
    second_client = NativeLogletClient("s2", members, transport)
    second = NativeLogletRuntime(second_sequencer, second_client, transport, members)
    meta_store = MemoryMetaStore()
    virtual_log = VirtualLog(
        meta_store,
        StaticLogletProvider({"s1": first, "s2": second}),
    )
    await virtual_log.bootstrap(segment("s1", 0, None))
    await virtual_log.append("r1", b"first")
    await virtual_log.reconfig_extend(
        NewLogletConfiguration(
            segment_id="s2",
            loglet=native_loglet_configuration(
                members,
                "db-2",
                "inc-s2",
            ),
        )
    )

    zombie = LogEntry(
        segment_id="s1",
        position=1,
        command_id="zombie",
        payload=b"must-not-be-visible",
    )
    await stores["db-1"].repair(zombie)
    await virtual_log.append("r2", b"second")

    entry = await virtual_log.read(1)
    assert entry.segment_id == "s2"
    assert entry.payload == b"second"
