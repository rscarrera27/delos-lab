import asyncio

import pytest

from delos_lab.metastore.memory import MemoryMetaStore
from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.native_loglet.virtual_log_adapter import NativeLogletRuntime
from delos_lab.virtual_log.core import PositionUnavailable, VirtualLog
from delos_lab.virtual_log.loglet import LogletUnavailable, StaticLogletProvider
from delos_lab.virtual_log.metastore import Applied
from delos_lab.virtual_log.types import (
    LogletConfigurationUpdate,
    LogSegment,
    NewLogletConfiguration,
)

MEMBERS = ("db-1", "db-2", "db-3")


def segment(segment_id: str) -> LogSegment:
    return LogSegment(
        segment_id=segment_id,
        virtual_start=0,
        virtual_stop=None,
        loglet=native_loglet_configuration(
            MEMBERS,
            "db-1",
            f"inc-{segment_id}",
        ),
    )


def configuration(segment_id: str, sequencer: str = "db-2") -> NewLogletConfiguration:
    return NewLogletConfiguration(
        segment_id=segment_id,
        loglet=native_loglet_configuration(
            MEMBERS,
            sequencer,
            f"inc-{segment_id}",
        ),
    )


def runtime(segment_id: str) -> NativeLogletRuntime:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    transport = DirectLogletTransport(stores)
    sequencer = NativeSequencer(segment_id, "db-1", MEMBERS, transport)
    client = NativeLogletClient(segment_id, MEMBERS, transport)
    return NativeLogletRuntime(sequencer, client, transport, MEMBERS)


class DynamicTestProvider:
    """Give every newly installed segment an independent in-memory Loglet."""

    def __init__(self) -> None:
        self.runtimes: dict[str, NativeLogletRuntime] = {}

    def get(self, selected: LogSegment) -> NativeLogletRuntime:
        existing = self.runtimes.get(selected.segment_id)
        if existing is not None:
            return existing
        created = runtime(selected.segment_id)
        self.runtimes[selected.segment_id] = created
        return created


class SimulatedClientCrash(Exception):
    pass


class CrashAtInstallMetaStore(MemoryMetaStore):
    def __init__(self, *, after_install: bool) -> None:
        super().__init__()
        self.after_install = after_install
        self.armed = False

    async def compare_and_set(self, expected_version: int, new_chain):
        if self.armed and not self.after_install:
            self.armed = False
            raise SimulatedClientCrash("before MetaStore install")
        result = await super().compare_and_set(expected_version, new_chain)
        if self.armed and self.after_install and isinstance(result, Applied):
            self.armed = False
            raise SimulatedClientCrash("after MetaStore install")
        return result


class RacingMetaStore(MemoryMetaStore):
    def __init__(self) -> None:
        super().__init__()
        self._arrivals = 0
        self._both_arrived = asyncio.Event()

    async def compare_and_set(self, expected_version: int, new_chain):
        if expected_version == 1:
            self._arrivals += 1
            if self._arrivals == 2:
                self._both_arrived.set()
            await self._both_arrived.wait()
        return await super().compare_and_set(expected_version, new_chain)


async def configured_virtual_log() -> tuple[VirtualLog, MemoryMetaStore]:
    meta_store = MemoryMetaStore()
    first_runtime = runtime("s1")
    virtual_log = VirtualLog(
        meta_store,
        StaticLogletProvider({"s1": first_runtime}),
    )
    await virtual_log.bootstrap(segment("s1"))
    return virtual_log, meta_store


async def test_reconfig_extend_seals_tail_and_extends_chain() -> None:
    virtual_log, meta_store = await configured_virtual_log()
    await virtual_log.append("request-1", b"first")
    await virtual_log.append("request-2", b"second")

    applied = await virtual_log.reconfig_extend(configuration("s2"))
    result = virtual_log.cached

    assert applied is True
    assert result.version == 2
    assert result.chain is not None
    assert result.chain.segments[0].virtual_stop == 2
    assert result.chain.active.segment_id == "s2"
    assert result.chain.active.virtual_start == 2
    assert await meta_store.read() == result


async def test_reconfig_extend_preserves_address_space_for_empty_segment() -> None:
    virtual_log, _ = await configured_virtual_log()

    assert await virtual_log.reconfig_extend(configuration("s2")) is True
    result = virtual_log.cached

    assert result.chain is not None
    assert result.chain.segments[0].virtual_stop == 0
    assert result.chain.active.virtual_start == 0


async def test_concurrent_reconfiguration_adopts_one_winning_chain() -> None:
    meta_store = MemoryMetaStore()
    first_runtime = runtime("s1")
    provider = StaticLogletProvider({"s1": first_runtime})
    left = VirtualLog(meta_store, provider)
    right = VirtualLog(meta_store, provider)
    await left.bootstrap(segment("s1"))
    await right.refresh()
    await left.append("request-1", b"first")

    left_result, right_result = await asyncio.gather(
        left.reconfig_extend(configuration("s-left", "db-2")),
        right.reconfig_extend(configuration("s-right", "db-3")),
    )

    assert {left_result, right_result} == {False, True}
    assert left.cached == right.cached
    assert left.cached.version == 2
    assert left.cached.chain is not None
    assert left.cached.chain.active.segment_id in {"s-left", "s-right"}
    assert await meta_store.read() == left.cached


async def test_other_client_rolls_forward_after_reconfigurer_crashes_after_seal() -> None:
    meta_store = MemoryMetaStore()
    provider = DynamicTestProvider()
    reconfigurer = VirtualLog(meta_store, provider, roll_forward_timeout=0)
    survivor = VirtualLog(meta_store, provider, roll_forward_timeout=0)
    installed = segment("s1")
    await reconfigurer.bootstrap(installed)
    await survivor.refresh()
    await reconfigurer.append("request-1", b"first")

    await reconfigurer.seal()
    position = await survivor.append("request-2", b"second")

    assert position == 1
    assert survivor.cached.version == 2
    assert survivor.cached.chain is not None
    assert survivor.cached.chain.segments[0].virtual_stop == 1
    assert survivor.cached.chain.active.segment_id != "s1"
    assert survivor.cached.chain.active.loglet == installed.loglet


async def test_other_client_rolls_forward_after_crash_between_check_tail_and_cas() -> None:
    meta_store = CrashAtInstallMetaStore(after_install=False)
    provider = DynamicTestProvider()
    reconfigurer = VirtualLog(meta_store, provider, roll_forward_timeout=0)
    survivor = VirtualLog(meta_store, provider, roll_forward_timeout=0)
    await reconfigurer.bootstrap(segment("s1"))
    await survivor.refresh()
    await reconfigurer.append("request-1", b"first")
    meta_store.armed = True

    with pytest.raises(SimulatedClientCrash, match="before"):
        await reconfigurer.reconfig_extend(configuration("abandoned"))

    assert provider.runtimes["s1"].last_check_tail is not None
    assert provider.runtimes["s1"].last_check_tail.sealed is True
    assert await survivor.append("request-2", b"second") == 1
    assert survivor.cached.version == 2
    assert survivor.cached.chain is not None
    assert survivor.cached.chain.active.segment_id not in {"s1", "abandoned"}


async def test_other_client_adopts_install_when_reconfigurer_crashes_before_response() -> None:
    meta_store = CrashAtInstallMetaStore(after_install=True)
    provider = DynamicTestProvider()
    reconfigurer = VirtualLog(meta_store, provider, roll_forward_timeout=0)
    survivor = VirtualLog(meta_store, provider, roll_forward_timeout=0)
    await reconfigurer.bootstrap(segment("s1"))
    await survivor.refresh()
    replacement = configuration("installed-before-crash", "db-3")
    meta_store.armed = True

    with pytest.raises(SimulatedClientCrash, match="after"):
        await reconfigurer.reconfig_extend(replacement)

    assert reconfigurer.cached.version == 1
    assert await survivor.append("request-1", b"first") == 0
    assert survivor.cached.version == 2
    assert survivor.cached.chain is not None
    assert survivor.cached.chain.active.segment_id == "installed-before-crash"
    assert survivor.cached.chain.active.loglet == replacement.loglet


async def test_generic_roll_forward_and_specific_successor_adopt_one_winner() -> None:
    meta_store = RacingMetaStore()
    provider = DynamicTestProvider()
    generic_client = VirtualLog(meta_store, provider, roll_forward_timeout=0)
    policy_client = VirtualLog(meta_store, provider, roll_forward_timeout=0)
    original = segment("s1")
    await generic_client.bootstrap(original)
    await policy_client.refresh()
    await generic_client.seal()
    specific = configuration("native-successor", "db-3")

    append_position, policy_applied = await asyncio.gather(
        generic_client.append("request-1", b"first"),
        policy_client.reconfig_extend(specific),
    )

    assert append_position == 0
    assert isinstance(policy_applied, bool)
    assert generic_client.cached == policy_client.cached
    assert policy_client.cached.chain is not None
    assert policy_client.cached.chain.active.loglet in (original.loglet, specific.loglet)
    assert await meta_store.read() == policy_client.cached


async def test_unavailable_loglet_uses_injected_specific_policy() -> None:
    class UnavailableOnceRuntime:
        def __init__(self, delegate: NativeLogletRuntime) -> None:
            self.delegate = delegate
            self.failed = False

        async def append(self, command_id: str, payload: bytes):
            if not self.failed:
                self.failed = True
                raise LogletUnavailable("sequencer unavailable")
            return await self.delegate.append(command_id, payload)

        async def seal(self) -> None:
            await self.delegate.seal()

        async def check_tail(self):
            return await self.delegate.check_tail()

        async def read_next(self, local_start: int, local_stop: int):
            return await self.delegate.read_next(local_start, local_stop)

    class RecordingPolicy:
        def __init__(self) -> None:
            self.failed: list[LogSegment] = []

        async def successor(self, failed: LogSegment) -> NewLogletConfiguration:
            self.failed.append(failed)
            return configuration("policy-successor", "db-3")

    first = UnavailableOnceRuntime(runtime("s1"))
    provider = StaticLogletProvider({"s1": first, "policy-successor": runtime("policy-successor")})
    policy = RecordingPolicy()
    log = VirtualLog(MemoryMetaStore(), provider, policy, roll_forward_timeout=0)
    await log.bootstrap(segment("s1"))

    assert await log.append("request-1", b"first") == 0
    assert policy.failed == [segment("s1")]
    assert log.cached.chain is not None
    assert log.cached.chain.active.segment_id == "policy-successor"


async def test_prefix_trim_removes_fully_trimmed_sealed_segment() -> None:
    provider = DynamicTestProvider()
    log = VirtualLog(MemoryMetaStore(), provider)
    await log.bootstrap(segment("s1"))
    await log.append("request-1", b"first")
    await log.append("request-2", b"second")
    assert await log.reconfig_extend(configuration("s2")) is True
    await log.append("request-3", b"third")

    assert await log.prefix_trim(2) == 2

    assert log.cached.version == 3
    assert log.cached.chain is not None
    assert tuple(item.segment_id for item in log.cached.chain.segments) == ("s2",)
    assert log.cached.chain.active.virtual_start == 2
    with pytest.raises(PositionUnavailable):
        await log.read(0)
    assert (await log.read(2)).payload == b"third"


async def test_prefix_trim_within_active_segment_preserves_tail_and_future_positions() -> None:
    provider = DynamicTestProvider()
    log = VirtualLog(MemoryMetaStore(), provider)
    await log.bootstrap(segment("s1"))
    await log.append("request-1", b"first")
    await log.append("request-2", b"second")

    assert await log.prefix_trim(1) == 1
    with pytest.raises(PositionUnavailable):
        await log.read(0)
    assert (await log.read(1)).payload == b"second"
    assert await log.append("request-3", b"third") == 2


async def test_prefix_trim_cannot_pass_virtual_tail() -> None:
    provider = DynamicTestProvider()
    log = VirtualLog(MemoryMetaStore(), provider)
    await log.bootstrap(segment("s1"))

    with pytest.raises(ValueError, match="cannot pass"):
        await log.prefix_trim(1)


async def test_concurrent_reconfig_truncate_adopts_one_winner() -> None:
    meta_store = MemoryMetaStore()
    provider = DynamicTestProvider()
    left = VirtualLog(meta_store, provider)
    right = VirtualLog(meta_store, provider)
    await left.bootstrap(segment("s1"))
    assert await left.reconfig_extend(configuration("s2")) is True
    await right.refresh()

    left_applied, right_applied = await asyncio.gather(
        left.reconfig_truncate(),
        right.reconfig_truncate(),
    )

    assert {left_applied, right_applied} == {False, True}
    assert left.cached == right.cached
    assert left.cached.chain is not None
    assert tuple(item.segment_id for item in left.cached.chain.segments) == ("s2",)


async def test_reconfig_modify_changes_only_a_sealed_segment_configuration() -> None:
    provider = DynamicTestProvider()
    log = VirtualLog(MemoryMetaStore(), provider)
    await log.bootstrap(segment("s1"))
    await log.append("request-1", b"first")
    assert await log.reconfig_extend(configuration("s2")) is True
    replacement = configuration("replacement", "db-3").loglet

    assert (
        await log.reconfig_modify(LogletConfigurationUpdate(segment_id="s1", loglet=replacement))
        is True
    )

    assert log.cached.chain is not None
    modified, active = log.cached.chain.segments
    assert modified.segment_id == "s1"
    assert modified.loglet == replacement
    assert (modified.virtual_start, modified.virtual_stop) == (0, 1)
    assert active.segment_id == "s2"
    assert (await log.read(0)).payload == b"first"


async def test_reconfig_modify_rejects_active_segment() -> None:
    log, _ = await configured_virtual_log()

    with pytest.raises(ValueError, match="active"):
        await log.reconfig_modify(
            LogletConfigurationUpdate(
                segment_id="s1",
                loglet=configuration("replacement").loglet,
            )
        )
