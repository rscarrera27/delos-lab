from delos_lab.metastore.memory import MemoryMetaStore
from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.config import NativeLogletConfiguration, native_loglet_configuration
from delos_lab.native_loglet.membership import NativeLogletStorageMembership
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.native_loglet.virtual_log_adapter import NativeLogletRuntime
from delos_lab.virtual_log.core import VirtualLog
from delos_lab.virtual_log.types import LogSegment


class Incarnations:
    async def incarnation(self, node_id: str) -> str | None:
        return f"inc-{node_id}"


class EligibleMembers:
    def __init__(self, members: tuple[str, ...]) -> None:
        self._members = members

    def active_members(self) -> tuple[str, ...]:
        return self._members


class Provider:
    def __init__(self) -> None:
        members = ("db-1", "db-2", "db-3", "db-4")
        self._stores = {member: MemoryLogletStore(member) for member in members}
        self._transport = DirectLogletTransport(self._stores)
        self._runtimes: dict[str, NativeLogletRuntime] = {}

    def get(self, segment: LogSegment) -> NativeLogletRuntime:
        runtime = self._runtimes.get(segment.segment_id)
        if runtime is not None:
            return runtime
        configuration = NativeLogletConfiguration.from_generic(segment.loglet)
        runtime = NativeLogletRuntime(
            NativeSequencer(
                segment.segment_id,
                configuration.sequencer_node,
                configuration.storage_members,
                self._transport,
            ),
            NativeLogletClient(
                segment.segment_id,
                configuration.storage_members,
                self._transport,
            ),
            self._transport,
            configuration.storage_members,
        )
        self._runtimes[segment.segment_id] = runtime
        return runtime


async def test_join_creates_a_new_segment_with_the_database_node_as_storage_member() -> None:
    members = ("db-1", "db-2", "db-3")
    virtual_log = VirtualLog(MemoryMetaStore(), Provider())
    await virtual_log.bootstrap(
        LogSegment(
            segment_id="s1",
            virtual_start=0,
            virtual_stop=None,
            loglet=native_loglet_configuration(members, "db-1", "inc-db-1"),
        )
    )

    joined = await NativeLogletStorageMembership(
        "db-4",
        virtual_log,
        Incarnations(),
    ).join()

    assert joined.chain is not None
    assert len(joined.chain.segments) == 2
    active = NativeLogletConfiguration.from_generic(joined.chain.active.loglet)
    assert active.storage_members == (*members, "db-4")
    assert active.sequencer_node == "db-4"


async def test_join_replaces_retired_storage_member_in_the_new_segment() -> None:
    members = ("db-1", "db-2", "db-3")
    virtual_log = VirtualLog(MemoryMetaStore(), Provider())
    await virtual_log.bootstrap(
        LogSegment(
            segment_id="s1",
            virtual_start=0,
            virtual_stop=None,
            loglet=native_loglet_configuration(members, "db-1", "inc-db-1"),
        )
    )

    joined = await NativeLogletStorageMembership(
        "db-4",
        virtual_log,
        Incarnations(),
        EligibleMembers(("db-1", "db-3", "db-4")),
    ).join()

    assert joined.chain is not None
    assert (
        NativeLogletConfiguration.from_generic(joined.chain.segments[0].loglet).storage_members
        == members
    )
    active = NativeLogletConfiguration.from_generic(joined.chain.active.loglet)
    assert active.storage_members == ("db-1", "db-3", "db-4")
