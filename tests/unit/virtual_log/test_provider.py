from delos_lab.metastore.memory import MemoryMetaStore
from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.native_loglet.virtual_log_adapter import NativeLogletRuntime
from delos_lab.virtual_log.core import VirtualLog
from delos_lab.virtual_log.loglet import StaticLogletProvider
from delos_lab.virtual_log.types import LogSegment


def segment() -> LogSegment:
    return LogSegment(
        segment_id="s1",
        virtual_start=0,
        virtual_stop=None,
        loglet=native_loglet_configuration(
            ("db-1", "db-2", "db-3"),
            "db-1",
            "inc-1",
        ),
    )


def runtime() -> NativeLogletRuntime:
    members = ("db-1", "db-2", "db-3")
    stores = {member: MemoryLogletStore(member) for member in members}
    transport = DirectLogletTransport(stores)
    sequencer = NativeSequencer("s1", "db-1", members, transport)
    client = NativeLogletClient("s1", members, transport)
    return NativeLogletRuntime(sequencer, client, transport, members)


async def test_virtual_log_passes_active_segment_to_provider() -> None:
    installed = segment()

    class RecordingProvider:
        def __init__(self) -> None:
            self.segments: list[LogSegment] = []
            self.runtime = runtime()

        def get(self, selected: LogSegment) -> NativeLogletRuntime:
            self.segments.append(selected)
            return self.runtime

    provider = RecordingProvider()
    virtual_log = VirtualLog(MemoryMetaStore(), provider)
    await virtual_log.bootstrap(installed)

    await virtual_log.append("r1", b"payload")

    assert provider.segments == [installed]


def test_static_provider_selects_runtime_by_segment_id() -> None:
    selected = runtime()

    assert StaticLogletProvider({"s1": selected}).get(segment()) is selected
