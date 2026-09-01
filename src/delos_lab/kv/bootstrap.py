from typing import Protocol

from .errors import SyncRequired
from .snapshot import KvSnapshot
from .sqlite_store import SQLiteKvStore


class SnapshotSource(Protocol):
    async def fetch(self) -> KvSnapshot: ...


class CatchUpApplication(Protocol):
    async def sync(self) -> int: ...


class DatabaseReplicaBootstrapper:
    """Install application state, then replay the VirtualLog through a fresh tail fence."""

    def __init__(
        self,
        store: SQLiteKvStore,
        source: SnapshotSource,
        application: CatchUpApplication,
        *,
        max_snapshot_attempts: int = 3,
    ) -> None:
        self._store = store
        self._source = source
        self._application = application
        self._max_snapshot_attempts = max_snapshot_attempts

    async def run(self) -> int:
        if await self._store.applied_position() < 0:
            await self._store.install_snapshot(await self._source.fetch())
        for attempt in range(self._max_snapshot_attempts):
            try:
                return await self._application.sync()
            except SyncRequired:
                if attempt + 1 == self._max_snapshot_attempts:
                    raise
                await self._store.replace_bootstrap_snapshot(await self._source.fetch())
        raise AssertionError("database bootstrap retry loop exhausted")
