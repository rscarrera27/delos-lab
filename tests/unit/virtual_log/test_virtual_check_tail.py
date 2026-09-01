from delos_lab.metastore.memory import MemoryMetaStore
from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.native_loglet.virtual_log_adapter import NativeLogletRuntime
from delos_lab.virtual_log.core import VirtualLog
from delos_lab.virtual_log.loglet import LogletTail, StaticLogletProvider
from delos_lab.virtual_log.types import LogChain, LogSegment

MEMBERS = ("db-1", "db-2", "db-3")


def segment(segment_id: str, start: int, end: int | None) -> LogSegment:
    return LogSegment(
        segment_id=segment_id,
        virtual_start=start,
        virtual_stop=end,
        loglet=native_loglet_configuration(
            MEMBERS,
            "db-1",
            f"inc-{segment_id}",
        ),
    )


def runtime(segment_id: str) -> NativeLogletRuntime:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    transport = DirectLogletTransport(stores)
    sequencer = NativeSequencer(segment_id, "db-1", MEMBERS, transport)
    client = NativeLogletClient(segment_id, MEMBERS, transport)
    return NativeLogletRuntime(sequencer, client, transport, MEMBERS)


async def configured_log(active_runtime: NativeLogletRuntime) -> VirtualLog:
    chain = LogChain(
        segments=(
            segment("s1", 0, 5),
            segment("s2", 5, None),
        )
    )
    meta_store = MemoryMetaStore()
    await meta_store.compare_and_set(0, chain)
    log = VirtualLog(meta_store, StaticLogletProvider({"s2": active_runtime}))
    await log.refresh()
    return log


async def test_check_tail_maps_first_uncommitted_tail_to_virtual_position() -> None:
    active = runtime("s2")
    await active.append("r1", b"first")
    await active.append("r2", b"second")
    await active.append("r3", b"third")
    log = await configured_log(active)

    assert await log.check_tail() == LogletTail(tail=8, sealed=False)


async def test_empty_active_segment_tail_is_its_virtual_start() -> None:
    log = await configured_log(runtime("s2"))

    assert await log.check_tail() == LogletTail(tail=5, sealed=False)


async def test_sealed_cached_segment_refreshes_to_the_new_active_segment() -> None:
    first = runtime("s1")
    second = runtime("s2")
    meta_store = MemoryMetaStore()
    log = VirtualLog(
        meta_store,
        StaticLogletProvider({"s1": first, "s2": second}),
    )
    await log.bootstrap(segment("s1", 0, None))
    await first.append("r1", b"first")
    await first.seal()
    await meta_store.compare_and_set(
        1,
        LogChain(segments=(segment("s1", 0, 1), segment("s2", 1, None))),
    )
    await second.append("r2", b"second")

    assert await log.check_tail() == LogletTail(tail=2, sealed=False)
    assert log.cached.chain is not None
    assert log.cached.chain.active.segment_id == "s2"
