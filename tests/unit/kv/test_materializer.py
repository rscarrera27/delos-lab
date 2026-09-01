from pathlib import Path

import pytest

from delos_lab.kv.errors import InvalidKvPayload, SyncRequired
from delos_lab.kv.materializer import KvMaterializer
from delos_lab.kv.sqlite_store import SQLiteKvStore
from delos_lab.kv.types import Increment, KvCommandEnvelope, KvResult, Put
from delos_lab.virtual_log.types import VirtualLogEntry


def command(request_id: str, operation: Put | Increment) -> KvCommandEnvelope:
    return KvCommandEnvelope(client_id="c", request_id=request_id, operation=operation)


class FakeVirtualLog:
    def __init__(self, entries: dict[int, bytes]) -> None:
        self.entries = entries
        self.read_ranges: list[tuple[int, int]] = []

    async def read_next(self, virtual_start: int, virtual_stop: int) -> VirtualLogEntry | None:
        self.read_ranges.append((virtual_start, virtual_stop))
        position = next(
            (
                candidate
                for candidate in sorted(self.entries)
                if virtual_start <= candidate < virtual_stop
            ),
            None,
        )
        if position is None:
            return None
        return VirtualLogEntry(
            position=position,
            command_id=f"command-{position}",
            payload=self.entries[position],
            segment_id="s1",
            local_position=position,
        )


async def opened_store(path: Path) -> SQLiteKvStore:
    store = SQLiteKvStore(path)
    await store.open()
    return store


async def test_materializer_reads_every_position_and_returns_target_result(tmp_path: Path) -> None:
    commands = [command("r1", Put(key="a", value=1)), command("r2", Increment(key="a", delta=2))]
    log = FakeVirtualLog({position: item.to_payload() for position, item in enumerate(commands)})
    store = await opened_store(tmp_path / "node.sqlite")

    result = await KvMaterializer(log, store).materialize_through(1)

    assert result == KvResult(code="APPLIED", value=3)
    assert log.read_ranges == [(0, 2), (1, 2)]
    assert await store.applied_position() == 1
    await store.close()


async def test_materializer_resumes_after_persisted_position(tmp_path: Path) -> None:
    commands = [command("r1", Put(key="a", value=1)), command("r2", Put(key="b", value=2))]
    store = await opened_store(tmp_path / "node.sqlite")
    await store.apply(0, commands[0])
    log = FakeVirtualLog({position: item.to_payload() for position, item in enumerate(commands)})

    await KvMaterializer(log, store).materialize_through(1)

    assert log.read_ranges == [(1, 2)]
    await store.close()


async def test_materializer_preserves_progress_for_invalid_or_missing_entry(tmp_path: Path) -> None:
    store = await opened_store(tmp_path / "node.sqlite")
    with pytest.raises(InvalidKvPayload):
        await KvMaterializer(FakeVirtualLog({0: b"not-json"}), store).materialize_through(0)
    with pytest.raises(SyncRequired):
        await KvMaterializer(FakeVirtualLog({}), store).materialize_through(0)
    assert await store.applied_position() == -1
    await store.close()


async def test_materializer_advances_across_sparse_log_positions(tmp_path: Path) -> None:
    first = command("r1", Put(key="a", value=1))
    second = command("r2", Increment(key="a", delta=2))
    log = FakeVirtualLog({2: first.to_payload(), 5: second.to_payload()})
    store = await opened_store(tmp_path / "node.sqlite")

    result = await KvMaterializer(log, store).materialize_through(5)

    assert result == KvResult(code="APPLIED", value=3)
    assert log.read_ranges == [(0, 6), (3, 6)]
    assert await store.applied_position() == 5
    await store.close()
