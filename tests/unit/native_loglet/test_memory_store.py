import pytest

from delos_lab.native_loglet.errors import (
    EntryConflict,
    PositionTrimmed,
    PredecessorUnavailable,
)
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.types import LogEntry, LogServerState


async def test_put_is_idempotent_but_rejects_conflicting_payload() -> None:
    store = MemoryLogletStore("db-1")
    first = LogEntry(segment_id="s", position=0, command_id="r1", payload=b"a")
    conflict = LogEntry(segment_id="s", position=0, command_id="r2", payload=b"b")

    await store.put(first, known_tail=0)
    await store.put(first, known_tail=0)

    with pytest.raises(EntryConflict):
        await store.put(conflict, known_tail=0)


async def test_store_reports_first_unwritten_local_tail_and_known_tail() -> None:
    store = MemoryLogletStore("db-1")
    first = LogEntry(segment_id="s", position=0, command_id="r1", payload=b"a")

    await store.put(first, known_tail=0)

    assert await store.state("s", known_tail=0) == LogServerState(
        segment_id="s",
        local_tail=1,
        known_tail=0,
        sealed=False,
    )


async def test_store_accepts_local_hole_only_with_known_tail_proof() -> None:
    store = MemoryLogletStore("db-1")
    second = LogEntry(segment_id="s", position=1, command_id="r2", payload=b"b")

    with pytest.raises(PredecessorUnavailable):
        await store.put(second, known_tail=0)

    await store.put(second, known_tail=1)

    assert (await store.state("s", known_tail=0)).local_tail == 2
    assert await store.get("s", 0) is None
    assert await store.get("s", 1) == second


async def test_store_lists_an_ordered_bounded_entry_range() -> None:
    store = MemoryLogletStore("db-1")
    entries = tuple(
        LogEntry(segment_id="s", position=position, command_id=f"r{position}", payload=b"x")
        for position in range(4)
    )
    for entry in entries:
        await store.put(entry)

    assert await store.entries("s", start=1, limit=2) == entries[1:3]


async def test_prefix_trim_is_monotonic_and_preserves_the_physical_tail() -> None:
    store = MemoryLogletStore("db-1")
    entries = tuple(
        LogEntry(segment_id="s", position=position, command_id=f"r{position}", payload=b"x")
        for position in range(3)
    )
    for entry in entries:
        await store.put(entry)

    assert await store.prefix_trim("s", 2) == 2
    assert await store.prefix_trim("s", 1) == 2
    assert await store.get("s", 0) is None
    assert await store.get("s", 1) is None
    assert await store.get("s", 2) == entries[2]
    assert (await store.state("s")).local_tail == 3

    with pytest.raises(PositionTrimmed):
        await store.repair(entries[1])
