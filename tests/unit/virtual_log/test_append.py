from delos_lab.metastore.memory import MemoryMetaStore
from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.native_loglet.virtual_log_adapter import NativeLogletRuntime
from delos_lab.virtual_log.core import VirtualLog
from delos_lab.virtual_log.loglet import StaticLogletProvider
from delos_lab.virtual_log.types import LogChain, LogSegment, VersionedLogChain


class CountingMemoryMetaStore(MemoryMetaStore):
    def __init__(self) -> None:
        super().__init__()
        self.read_count = 0

    async def read(self) -> VersionedLogChain:
        self.read_count += 1
        return await super().read()


def segment(
    segment_id: str,
    start: int = 0,
    end: int | None = None,
) -> LogSegment:
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


def runtime(segment_id: str) -> NativeLogletRuntime:
    members = ("db-1", "db-2", "db-3")
    stores = {member: MemoryLogletStore(member) for member in members}
    transport = DirectLogletTransport(stores)
    sequencer = NativeSequencer(segment_id, "db-1", members, transport)
    client = NativeLogletClient(segment_id, members, transport)
    return NativeLogletRuntime(sequencer, client, transport, members)


async def test_append_maps_local_positions_without_reading_metastore() -> None:
    meta_store = CountingMemoryMetaStore()
    provider = StaticLogletProvider({"s1": runtime("s1")})
    virtual_log = VirtualLog(meta_store, provider)
    await virtual_log.bootstrap(segment("s1"))

    first = await virtual_log.append("request-1", b"first")
    second = await virtual_log.append("request-2", b"second")

    assert (first, second) == (0, 1)
    assert meta_store.read_count == 0


async def test_append_refreshes_and_retries_when_active_segment_changed() -> None:
    meta_store = CountingMemoryMetaStore()
    first_runtime = runtime("s1")
    second_runtime = runtime("s2")
    virtual_log = VirtualLog(
        meta_store,
        StaticLogletProvider({"s1": first_runtime, "s2": second_runtime}),
    )
    await virtual_log.bootstrap(segment("s1"))
    await virtual_log.append("request-1", b"first")
    await first_runtime.seal()
    await meta_store.compare_and_set(
        1,
        LogChain(segments=(segment("s1", 0, 1), segment("s2", 1))),
    )

    position = await virtual_log.append("request-2", b"second")

    assert position == 1
    assert virtual_log.cached.version == 2
    assert meta_store.read_count == 1


async def test_append_rolls_forward_when_sealed_chain_has_not_changed() -> None:
    meta_store = CountingMemoryMetaStore()
    first_runtime = runtime("s1")

    class CloningProvider:
        def __init__(self) -> None:
            self.runtimes = {"s1": first_runtime}

        def get(self, selected: LogSegment) -> NativeLogletRuntime:
            existing = self.runtimes.get(selected.segment_id)
            if existing is not None:
                return existing
            created = runtime(selected.segment_id)
            self.runtimes[selected.segment_id] = created
            return created

    virtual_log = VirtualLog(
        meta_store,
        CloningProvider(),
        roll_forward_timeout=0,
    )
    await virtual_log.bootstrap(segment("s1"))
    await first_runtime.seal()

    position = await virtual_log.append("request-1", b"first")

    assert position == 0
    assert virtual_log.cached.version == 2
    assert virtual_log.cached.chain is not None
    assert virtual_log.cached.chain.active.segment_id != "s1"
    assert virtual_log.cached.chain.active.loglet == segment("s1").loglet
    assert meta_store.read_count == 2
