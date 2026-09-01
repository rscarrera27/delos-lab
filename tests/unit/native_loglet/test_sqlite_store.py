import pytest

from delos_lab.native_loglet.errors import PositionTrimmed, PredecessorUnavailable
from delos_lab.native_loglet.sqlite_store import SQLiteLogletStore
from delos_lab.native_loglet.types import LogEntry


async def test_sqlite_store_preserves_entry_and_seal(tmp_path) -> None:
    path = tmp_path / "node.sqlite"
    entry = LogEntry(segment_id="s", position=0, command_id="r1", payload=b"a")
    first = SQLiteLogletStore("db-1", path)
    await first.open()
    await first.put(entry, known_tail=0)
    await first.seal("s", known_tail=1)
    assert (await first.state("s", known_tail=0)).known_tail == 1
    await first.close()

    restarted = SQLiteLogletStore("db-1", path)
    await restarted.open()
    restarted_state = await restarted.state("s", known_tail=0)
    assert restarted_state.sealed is True
    assert restarted_state.local_tail == 1
    assert restarted_state.known_tail == 0
    assert await restarted.get("s", 0) == entry
    await restarted.close()


async def test_sqlite_store_fences_holes_without_known_tail(tmp_path) -> None:
    store = SQLiteLogletStore("db-1", tmp_path / "node.sqlite")
    await store.open()
    second = LogEntry(segment_id="s", position=1, command_id="r2", payload=b"b")

    with pytest.raises(PredecessorUnavailable):
        await store.put(second, known_tail=0)

    await store.put(second, known_tail=1)
    assert (await store.state("s", known_tail=0)).local_tail == 2
    await store.close()


async def test_sqlite_store_lists_an_ordered_bounded_entry_range(tmp_path) -> None:
    store = SQLiteLogletStore("db-1", tmp_path / "node.sqlite")
    await store.open()
    entries = tuple(
        LogEntry(segment_id="s", position=position, command_id=f"r{position}", payload=b"x")
        for position in range(4)
    )
    for entry in entries:
        await store.put(entry)

    assert await store.entries("s", start=1, limit=2) == entries[1:3]
    await store.close()


async def test_sqlite_prefix_trim_survives_restart_without_rewinding_tail(tmp_path) -> None:
    path = tmp_path / "node.sqlite"
    entries = tuple(
        LogEntry(segment_id="s", position=position, command_id=f"r{position}", payload=b"x")
        for position in range(3)
    )
    store = SQLiteLogletStore("db-1", path)
    await store.open()
    for entry in entries:
        await store.put(entry)
    assert await store.prefix_trim("s", 3) == 3
    await store.close()

    restarted = SQLiteLogletStore("db-1", path)
    await restarted.open()
    state = await restarted.state("s")
    assert (state.local_tail, state.trimmed_prefix) == (3, 3)
    assert await restarted.entries("s") == ()
    with pytest.raises(PositionTrimmed):
        await restarted.repair(entries[0])
    await restarted.close()
