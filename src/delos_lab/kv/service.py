import asyncio
from typing import Protocol

from delos_lab.virtual_log.core import ChainUnavailable
from delos_lab.virtual_log.loglet import LogletSealed, LogletTail, LogletUnavailable
from delos_lab.virtual_log.types import (
    LogSegment,
    VersionedLogChain,
)

from .errors import ReconfigurationUnavailable
from .materializer import KvMaterializer
from .snapshot import KvSnapshot
from .sqlite_store import RequestConflict, SQLiteKvStore
from .types import KvCommandEnvelope, KvResult, KvValue


class ServiceVirtualLog(Protocol):
    @property
    def cached(self) -> VersionedLogChain: ...

    async def bootstrap(self, initial_segment: LogSegment) -> VersionedLogChain: ...

    async def refresh(self) -> VersionedLogChain: ...

    async def append(self, command_id: str, payload: bytes) -> int: ...

    async def check_tail(self) -> LogletTail: ...


class LogletBootstrapPolicy(Protocol):
    async def initial_segment(self) -> LogSegment: ...


class KvService:
    def __init__(
        self,
        node_id: str,
        virtual_log: ServiceVirtualLog,
        materializer: KvMaterializer,
        store: SQLiteKvStore,
        bootstrap: LogletBootstrapPolicy,
    ) -> None:
        self.node_id = node_id
        self.virtual_log = virtual_log
        self._materializer = materializer
        self.store = store
        self._bootstrap = bootstrap
        self._bootstrap_lock = asyncio.Lock()

    async def ensure_bootstrapped(self) -> VersionedLogChain:
        try:
            return self.virtual_log.cached
        except ChainUnavailable:
            pass
        async with self._bootstrap_lock:
            try:
                return await self.virtual_log.refresh()
            except ChainUnavailable:
                try:
                    initial = await self._bootstrap.initial_segment()
                except LogletUnavailable as error:
                    raise ReconfigurationUnavailable(
                        "initial Loglet configuration is unavailable"
                    ) from error
                return await self.virtual_log.bootstrap(initial)

    async def submit(self, command: KvCommandEnvelope) -> KvResult:
        completed = await self.store.request(command.client_id, command.request_id)
        if completed is not None:
            previous, result = completed
            if previous != command:
                raise RequestConflict(command.command_id)
            return result

        await self.ensure_bootstrapped()
        try:
            position = await self.virtual_log.append(command.command_id, command.to_payload())
        except (LogletSealed, LogletUnavailable) as error:
            raise ReconfigurationUnavailable(f"could not append {command.command_id}") from error
        return await self._materializer.materialize_through(position)

    async def sync(self) -> int:
        await self.ensure_bootstrapped()
        try:
            tail = await self.virtual_log.check_tail()
        except (LogletSealed, LogletUnavailable) as error:
            raise ReconfigurationUnavailable("could not establish the Loglet tail") from error
        if tail.sealed:
            raise ReconfigurationUnavailable("VirtualLog returned a sealed active Loglet")
        target = tail.tail - 1
        if target > await self.store.applied_position():
            await self._materializer.materialize_through(target)
        return target

    async def get(self, key: str) -> KvValue | None:
        await self.sync()
        return await self.store.get(key)

    async def export_bootstrap_snapshot(self) -> KvSnapshot:
        """Return application state at a position that this replica has materialized."""
        await self.sync()
        return await self.store.export_snapshot()

    def cached_chain(self) -> VersionedLogChain | None:
        try:
            return self.virtual_log.cached
        except ChainUnavailable:
            return None
