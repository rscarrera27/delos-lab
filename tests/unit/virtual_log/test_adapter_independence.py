from delos_lab.metastore.memory import MemoryMetaStore
from delos_lab.virtual_log.core import VirtualLog
from delos_lab.virtual_log.loglet import (
    LogletAppend,
    LogletEntry,
    LogletTail,
    StaticLogletProvider,
)
from delos_lab.virtual_log.types import (
    LogletConfiguration,
    LogSegment,
    NewLogletConfiguration,
)


class MemoryRuntime:
    def __init__(self) -> None:
        self.entries: list[LogletEntry] = []
        self.tail = 0
        self.sealed = False

    @property
    def known_tail(self) -> int:
        return self.tail

    @property
    def last_check_tail(self) -> LogletTail | None:
        return None

    async def append(self, command_id: str, payload: bytes) -> LogletAppend:
        position = self.tail
        self.entries.append(LogletEntry(position=position, command_id=command_id, payload=payload))
        self.tail += 1
        return LogletAppend(position=position, known_tail=self.tail)

    async def seal(self) -> None:
        self.sealed = True

    async def check_tail(self) -> LogletTail:
        return LogletTail(tail=self.tail, sealed=self.sealed)

    async def prefix_trim(self, trim_position: int) -> int:
        self.entries = [entry for entry in self.entries if entry.position >= trim_position]
        return trim_position

    async def read_next(self, local_start: int, local_stop: int) -> LogletEntry | None:
        return next(
            (entry for entry in self.entries if local_start <= entry.position < local_stop),
            None,
        )


class MemoryProvider:
    """Resolve fresh test Loglets without interpreting their opaque parameters."""

    def __init__(self) -> None:
        self.runtimes: dict[str, MemoryRuntime] = {}

    def get(self, segment: LogSegment) -> MemoryRuntime:
        return self.runtimes.setdefault(segment.segment_id, MemoryRuntime())


def configuration(name: str) -> LogletConfiguration:
    return LogletConfiguration(kind="memory-test", version=1, parameters={"name": name})


async def test_virtual_log_accepts_a_non_native_loglet_adapter() -> None:
    first = MemoryRuntime()
    second = MemoryRuntime()
    log = VirtualLog(
        MemoryMetaStore(),
        StaticLogletProvider({"first": first, "second": second}),
    )
    await log.bootstrap(
        LogSegment(
            segment_id="first",
            virtual_start=0,
            virtual_stop=None,
            loglet=configuration("first"),
        )
    )

    assert await log.append("one", b"first") == 0
    assert (
        await log.reconfig_extend(
            NewLogletConfiguration(segment_id="second", loglet=configuration("second"))
        )
        is True
    )
    snapshot = log.cached
    assert snapshot.chain is not None
    assert snapshot.chain.segments[0].virtual_stop == 1
    assert snapshot.chain.active.virtual_start == 1
    assert await log.append("two", b"second") == 1
    assert (await log.read(0)).payload == b"first"
    assert (await log.read(1)).payload == b"second"


async def test_virtual_log_read_next_preserves_a_sparse_adapter_position() -> None:
    runtime = MemoryRuntime()
    runtime.entries.append(LogletEntry(position=4, command_id="sparse", payload=b"value"))
    log = VirtualLog(MemoryMetaStore(), StaticLogletProvider({"only": runtime}))
    await log.bootstrap(
        LogSegment(
            segment_id="only",
            virtual_start=0,
            virtual_stop=None,
            loglet=configuration("only"),
        )
    )

    entry = await log.read_next(0, 5)

    assert entry is not None
    assert (entry.position, entry.local_position, entry.payload) == (4, 4, b"value")


async def test_virtual_log_exposes_the_shared_seal_contract() -> None:
    provider = MemoryProvider()
    log = VirtualLog(
        MemoryMetaStore(),
        provider,
        roll_forward_timeout=0,
    )
    await log.bootstrap(
        LogSegment(
            segment_id="only",
            virtual_start=0,
            virtual_stop=None,
            loglet=configuration("only"),
        )
    )

    await log.seal()

    assert provider.runtimes["only"].sealed is True
    assert await log.check_tail() == LogletTail(tail=0, sealed=False)
    assert log.cached.chain is not None
    assert log.cached.chain.active.segment_id != "only"
    assert log.cached.chain.active.loglet == configuration("only")
