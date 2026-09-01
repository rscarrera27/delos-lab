import asyncio
from typing import Protocol

from pydantic import ValidationError

from delos_lab.virtual_log.types import VirtualLogEntry

from .errors import InvalidKvPayload, SyncRequired
from .sqlite_store import SQLiteKvStore
from .types import KvCommandEnvelope, KvResult


class ReadableVirtualLog(Protocol):
    async def read_next(self, virtual_start: int, virtual_stop: int) -> VirtualLogEntry | None: ...


class KvMaterializer:
    def __init__(self, virtual_log: ReadableVirtualLog, store: SQLiteKvStore) -> None:
        self._virtual_log = virtual_log
        self._store = store
        self._lock = asyncio.Lock()

    async def materialize_through(self, target: int) -> KvResult:
        async with self._lock:
            current = await self._store.applied_position()
            if target <= current:
                existing = await self._store.result_at_position(target)
                if existing is None:
                    raise SyncRequired(f"position {target} has no stored result")
                return existing

            result: KvResult | None = None
            next_position = current + 1
            while next_position <= target:
                try:
                    entry = await self._virtual_log.read_next(next_position, target + 1)
                except ConnectionError as error:
                    raise SyncRequired(
                        f"range [{next_position}, {target + 1}) is unavailable"
                    ) from error
                if entry is None:
                    raise SyncRequired(
                        f"readNext found no entry in committed range "
                        f"[{next_position}, {target + 1})"
                    )
                try:
                    command = KvCommandEnvelope.from_payload(entry.payload)
                except (ValidationError, ValueError) as error:
                    raise InvalidKvPayload(
                        f"position {entry.position} has invalid payload"
                    ) from error
                result = await self._store.apply(entry.position, command)
                next_position = entry.position + 1
            if result is None:
                raise SyncRequired(f"position {target} was not applied")
            return result
