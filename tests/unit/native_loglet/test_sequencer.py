import asyncio

from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.sequencer_registry import (
    LogServerSequencerRegistry,
    SequencerObservation,
)
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.virtual_log.types import LogSegment
from tests.support.native_loglet import NativeLogletScenario


async def test_sequencer_assigns_positions_and_deduplicates_request() -> None:
    scenario = NativeLogletScenario()

    first = await scenario.append("r1", b"a")
    duplicate = await scenario.append("r1", b"a")
    second = await scenario.append("r2", b"b")

    assert (first.position, duplicate.position, second.position) == (0, 0, 1)
    assert (first.known_tail, duplicate.known_tail, second.known_tail) == (1, 1, 2)


async def test_sequencer_commits_with_one_failure_and_retries_same_position() -> None:
    scenario = NativeLogletScenario()

    scenario.disconnect("db-3")
    assert (await scenario.append("r1", b"a")).position == 0

    scenario.disconnect("db-2")
    pending = asyncio.create_task(scenario.append("r2", b"b"))
    await asyncio.sleep(0.02)
    assert not pending.done()

    scenario.reconnect("db-2")
    assert (await asyncio.wait_for(pending, timeout=1)).position == 1


async def test_sequencer_commits_pending_entry_before_next_command() -> None:
    scenario = NativeLogletScenario()

    scenario.disconnect("db-2", "db-3")
    first = asyncio.create_task(scenario.append("r1", b"first"))
    second = asyncio.create_task(scenario.append("r2", b"second"))
    await asyncio.sleep(0.02)
    assert not first.done()
    assert not second.done()

    scenario.reconnect("db-2", "db-3")
    first_result, second_result = await asyncio.wait_for(
        asyncio.gather(first, second),
        timeout=1,
    )

    assert (first_result.position, second_result.position) == (0, 1)
    assert scenario.sequencer_known_tail == 2
    for node_id in scenario.members:
        first = await scenario.entry_on(node_id, 0)
        committed_second = await scenario.entry_on(node_id, 1)
        assert first is not None
        assert committed_second is not None
        assert first.payload == b"first"
        assert committed_second.payload == b"second"


def test_sequencer_uses_a_strict_majority_for_even_membership() -> None:
    stores = {name: MemoryLogletStore(name) for name in ("db-1", "db-2", "db-3", "db-4")}

    sequencer = NativeSequencer("s", "db-1", tuple(stores), DirectLogletTransport(stores))

    assert sequencer.quorum == 3


async def test_registry_observes_sequencer_known_tail_without_creating_append_state() -> None:
    members = ("db-1", "db-2", "db-3")
    stores = {name: MemoryLogletStore(name) for name in members}
    registry = LogServerSequencerRegistry("db-1", "inc-1", DirectLogletTransport(stores))
    segment = LogSegment(
        segment_id="s",
        virtual_start=0,
        virtual_stop=None,
        loglet=native_loglet_configuration(members, "db-1", "inc-1"),
    )

    assert await registry.observe(segment) == SequencerObservation("s", 0)
    await registry.append(segment, "r1", b"value")
    assert await registry.observe(segment) == SequencerObservation("s", 1)

    await registry.close()
