from pathlib import Path

from delos_lab.kv.bootstrap import DatabaseReplicaBootstrapper
from delos_lab.kv.errors import SyncRequired
from delos_lab.kv.snapshot import KvSnapshot
from delos_lab.kv.sqlite_store import SQLiteKvStore
from delos_lab.kv.types import KvCommandEnvelope, Put


class Source:
    def __init__(self, snapshot: KvSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def fetch(self) -> KvSnapshot:
        self.calls += 1
        return self.snapshot


class Application:
    def __init__(self, target: int, *, sync_required_once: bool = False) -> None:
        self.target = target
        self.calls = 0
        self.sync_required_once = sync_required_once

    async def sync(self) -> int:
        self.calls += 1
        if self.sync_required_once:
            self.sync_required_once = False
            raise SyncRequired("required suffix was trimmed")
        return self.target


async def opened_store(path: Path) -> SQLiteKvStore:
    store = SQLiteKvStore(path)
    await store.open()
    return store


async def test_bootstrap_installs_snapshot_before_virtual_log_catch_up(tmp_path: Path) -> None:
    source_store = await opened_store(tmp_path / "source.sqlite")
    command = KvCommandEnvelope(
        client_id="client",
        request_id="put",
        operation=Put(key="colour", value="blue"),
    )
    await source_store.apply(4, command)
    source = Source(await source_store.export_snapshot())
    target_store = await opened_store(tmp_path / "target.sqlite")
    application = Application(6)

    result = await DatabaseReplicaBootstrapper(target_store, source, application).run()

    assert result == 6
    assert await target_store.applied_position() == 4
    assert await target_store.get("colour") == "blue"
    assert source.calls == application.calls == 1
    await target_store.close()
    await source_store.close()


async def test_bootstrap_resumes_materialized_replica_without_reinstalling(tmp_path: Path) -> None:
    store = await opened_store(tmp_path / "target.sqlite")
    await store.apply(
        0,
        KvCommandEnvelope(
            client_id="client",
            request_id="put",
            operation=Put(key="colour", value="blue"),
        ),
    )
    source = Source(await store.export_snapshot())
    application = Application(2)

    assert await DatabaseReplicaBootstrapper(store, source, application).run() == 2
    assert source.calls == 0
    assert application.calls == 1
    await store.close()


async def test_bootstrap_rebases_from_a_new_snapshot_when_the_required_suffix_is_trimmed(
    tmp_path: Path,
) -> None:
    old_source = await opened_store(tmp_path / "old-source.sqlite")
    await old_source.apply(
        0,
        KvCommandEnvelope(
            client_id="client",
            request_id="old",
            operation=Put(key="colour", value="blue"),
        ),
    )
    new_source = await opened_store(tmp_path / "new-source.sqlite")
    await new_source.apply(
        3,
        KvCommandEnvelope(
            client_id="client",
            request_id="new",
            operation=Put(key="colour", value="green"),
        ),
    )
    snapshots = [await old_source.export_snapshot(), await new_source.export_snapshot()]

    class AdvancingSource:
        async def fetch(self) -> KvSnapshot:
            return snapshots.pop(0)

    target = await opened_store(tmp_path / "target.sqlite")
    application = Application(3, sync_required_once=True)

    assert await DatabaseReplicaBootstrapper(target, AdvancingSource(), application).run() == 3
    assert await target.applied_position() == 3
    assert await target.get("colour") == "green"
    assert application.calls == 2
    await target.close()
    await new_source.close()
    await old_source.close()
