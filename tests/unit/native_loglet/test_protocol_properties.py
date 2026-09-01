import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.types import CheckTailResult, LogEntry
from tests.support.native_loglet import NativeLogletScenario

MEMBERS = NativeLogletScenario.members


@settings(max_examples=40, deadline=None)
@given(
    failure_schedule=st.lists(
        st.sets(st.sampled_from(MEMBERS), max_size=len(MEMBERS)),
        min_size=1,
        max_size=8,
    )
)
async def test_append_retries_preserve_dense_positions_across_failure_schedules(
    failure_schedule: list[set[str]],
) -> None:
    scenario = NativeLogletScenario(retry_interval=0)

    for position, unavailable in enumerate(failure_schedule):
        scenario.transport.unavailable = set(unavailable)
        pending = asyncio.create_task(scenario.append(f"r{position}", bytes([position])))
        await asyncio.sleep(0)

        if len(unavailable) >= 2:
            assert not pending.done()
            scenario.transport.unavailable.clear()

        result = await asyncio.wait_for(pending, timeout=1)
        assert (result.position, result.known_tail) == (position, position + 1)

        copies = sum(
            [await scenario.entry_on(node_id, position) is not None for node_id in scenario.members]
        )
        assert copies >= 2


@settings(max_examples=20, deadline=None)
@given(copy_holders=st.sets(st.sampled_from(MEMBERS), min_size=1))
async def test_sealed_check_tail_repairs_any_single_sequencer_value(
    copy_holders: set[str],
) -> None:
    scenario = NativeLogletScenario()
    entry = LogEntry(
        segment_id=scenario.segment_id,
        position=0,
        command_id="zombie",
        payload=b"value",
    )
    for node_id in copy_holders:
        await scenario.transport.repair(node_id, entry)
    await scenario.seal(*scenario.members)

    assert await scenario.check_tail() == CheckTailResult(tail=1, sealed=True)
    assert all([await scenario.entry_on(node_id, 0) == entry for node_id in scenario.members])


@settings(max_examples=30, deadline=None)
@given(trim_requests=st.lists(st.integers(min_value=0, max_value=5), max_size=20))
async def test_trim_watermark_never_rewinds_under_random_requests(
    trim_requests: list[int],
) -> None:
    store = MemoryLogletStore("db-1")
    for position in range(5):
        await store.put(
            LogEntry(
                segment_id="s",
                position=position,
                command_id=f"r{position}",
                payload=b"value",
            )
        )

    expected = 0
    for requested in trim_requests:
        expected = max(expected, requested)
        assert await store.prefix_trim("s", requested) == expected
        state = await store.state("s")
        assert state.trimmed_prefix == expected
        assert state.local_tail >= expected
