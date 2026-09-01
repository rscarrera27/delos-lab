from pathlib import Path

import pytest

from delos_lab.kv.sqlite_store import RequestConflict, SQLiteKvStore
from delos_lab.kv.types import Increment, KvCommandEnvelope, KvResult, Put


def envelope(request_id: str, value: int) -> KvCommandEnvelope:
    return KvCommandEnvelope(
        client_id="client",
        request_id=request_id,
        operation=Put(key="n", value=value),
    )


async def opened_store(path: Path) -> SQLiteKvStore:
    store = SQLiteKvStore(path)
    await store.open()
    return store


async def test_apply_persists_value_result_and_position_atomically(tmp_path: Path) -> None:
    store = await opened_store(tmp_path / "node.sqlite")
    command = envelope("r1", 1)

    first = await store.apply(0, command)
    duplicate = await store.apply(0, command)

    assert first == duplicate == KvResult(code="APPLIED", value=1)
    assert await store.get("n") == 1
    assert await store.applied_position() == 0
    assert await store.result_at_position(0) == first
    await store.close()


async def test_request_count_counts_unique_materialized_requests(tmp_path: Path) -> None:
    store = await opened_store(tmp_path / "node.sqlite")

    assert await store.request_count() == 0

    await store.apply(0, envelope("r1", 1))
    await store.apply(1, envelope("r2", 2))

    assert await store.request_count() == 2
    await store.close()


async def test_apply_accepts_sparse_position_and_rejects_conflicting_request(
    tmp_path: Path,
) -> None:
    store = await opened_store(tmp_path / "node.sqlite")
    await store.apply(0, envelope("r1", 1))

    await store.apply(2, envelope("r2", 2))
    assert await store.applied_position() == 2
    with pytest.raises(RequestConflict):
        await store.apply(3, envelope("r1", 9))
    await store.close()


async def test_duplicate_at_later_position_advances_without_reapplying(tmp_path: Path) -> None:
    store = await opened_store(tmp_path / "node.sqlite")
    command = KvCommandEnvelope(
        client_id="client",
        request_id="increment",
        operation=Increment(key="n", delta=2),
    )

    first = await store.apply(0, command)
    repeated = await store.apply(1, command)

    assert first == repeated == KvResult(code="APPLIED", value=2)
    assert await store.get("n") == 2
    assert await store.applied_position() == 1
    assert await store.result_at_position(1) == repeated
    await store.close()


async def test_restart_preserves_progress_and_deduplication(tmp_path: Path) -> None:
    path = tmp_path / "node.sqlite"
    command = KvCommandEnvelope(
        client_id="client",
        request_id="r1",
        operation=Increment(key="n", delta=2),
    )
    first = await opened_store(path)
    await first.apply(0, command)
    await first.close()

    restarted = await opened_store(path)

    assert await restarted.apply(0, command) == KvResult(code="APPLIED", value=2)
    assert await restarted.get("n") == 2
    assert await restarted.applied_position() == 0
    assert await restarted.request("client", "r1") == (command, KvResult(code="APPLIED", value=2))
    await restarted.close()


async def test_snapshot_transfers_values_progress_results_and_request_deduplication(
    tmp_path: Path,
) -> None:
    source = await opened_store(tmp_path / "source.sqlite")
    increment = KvCommandEnvelope(
        client_id="client",
        request_id="increment",
        operation=Increment(key="n", delta=2),
    )
    await source.apply(0, increment)
    await source.apply(2, envelope("put", 7))
    snapshot = await source.export_snapshot()

    target = await opened_store(tmp_path / "target.sqlite")
    await target.install_snapshot(snapshot)

    assert await target.snapshot() == {"n": 7}
    assert await target.applied_position() == 2
    assert await target.result_at_position(0) == KvResult(code="APPLIED", value=2)
    assert await target.request("client", "increment") == (
        increment,
        KvResult(code="APPLIED", value=2),
    )
    assert await target.apply(3, increment) == KvResult(code="APPLIED", value=2)
    assert await target.get("n") == 7
    await target.close()
    await source.close()


async def test_snapshot_install_rejects_a_non_pristine_store(tmp_path: Path) -> None:
    source = await opened_store(tmp_path / "source.sqlite")
    await source.apply(0, envelope("source", 1))
    snapshot = await source.export_snapshot()
    target = await opened_store(tmp_path / "target.sqlite")
    await target.apply(0, envelope("target", 2))

    with pytest.raises(RuntimeError, match="pristine"):
        await target.install_snapshot(snapshot)

    assert await target.snapshot() == {"n": 2}
    await target.close()
    await source.close()
