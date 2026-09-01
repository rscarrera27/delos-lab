import asyncio
from typing import Protocol

from .store import LogletStore
from .types import LogEntry, LogServerState


class LogServer(Protocol):
    """NativeLoglet data-plane service, independent of its transport."""

    node_id: str

    async def put(self, entry: LogEntry, known_tail: int = 0) -> LogServerState: ...

    async def repair(self, entry: LogEntry, known_tail: int = 0) -> LogServerState: ...

    async def get(self, segment_id: str, position: int, known_tail: int = 0) -> LogEntry | None: ...

    async def entries(
        self, segment_id: str, start: int = 0, limit: int = 100
    ) -> tuple[LogEntry, ...]: ...

    async def prefix_trim(self, segment_id: str, trim_position: int) -> LogServerState: ...

    async def seal(self, segment_id: str, known_tail: int = 0) -> LogServerState: ...

    async def state(self, segment_id: str, known_tail: int = 0) -> LogServerState: ...

    async def segment_states(self) -> tuple[LogServerState, ...]: ...

    async def wait_for_tail(
        self,
        segment_id: str,
        local_tail: int,
        known_tail: int = 0,
    ) -> LogServerState: ...


class NativeLogServer:
    """Own LogServer protocol behavior while delegating durability to a store.

    Tail notifications are deliberately outside ``LogletStore``: waiting and
    request cancellation are service concerns, not storage-engine concerns.
    """

    def __init__(self, store: LogletStore) -> None:
        self.node_id = store.node_id
        self._store = store
        self._conditions: dict[str, asyncio.Condition] = {}

    def _condition(self, segment_id: str) -> asyncio.Condition:
        return self._conditions.setdefault(segment_id, asyncio.Condition())

    async def _notify(self, segment_id: str) -> None:
        async with self._condition(segment_id):
            self._condition(segment_id).notify_all()

    async def put(self, entry: LogEntry, known_tail: int = 0) -> LogServerState:
        try:
            await self._store.put(entry, known_tail)
            return await self._store.state(entry.segment_id, known_tail)
        finally:
            await self._notify(entry.segment_id)

    async def repair(self, entry: LogEntry, known_tail: int = 0) -> LogServerState:
        try:
            await self._store.repair(entry, known_tail)
            return await self._store.state(entry.segment_id, known_tail)
        finally:
            await self._notify(entry.segment_id)

    async def get(self, segment_id: str, position: int, known_tail: int = 0) -> LogEntry | None:
        return await self._store.get(segment_id, position, known_tail)

    async def entries(
        self, segment_id: str, start: int = 0, limit: int = 100
    ) -> tuple[LogEntry, ...]:
        return await self._store.entries(segment_id, start, limit)

    async def prefix_trim(self, segment_id: str, trim_position: int) -> LogServerState:
        try:
            await self._store.prefix_trim(segment_id, trim_position)
            return await self._store.state(segment_id)
        finally:
            await self._notify(segment_id)

    async def seal(self, segment_id: str, known_tail: int = 0) -> LogServerState:
        try:
            await self._store.seal(segment_id, known_tail)
            return await self._store.state(segment_id, known_tail)
        finally:
            await self._notify(segment_id)

    async def state(self, segment_id: str, known_tail: int = 0) -> LogServerState:
        return await self._store.state(segment_id, known_tail)

    async def segment_states(self) -> tuple[LogServerState, ...]:
        return await self._store.segment_states()

    async def wait_for_tail(
        self,
        segment_id: str,
        local_tail: int,
        known_tail: int = 0,
    ) -> LogServerState:
        """Wait until the local tail reaches ``local_tail`` or the segment seals."""
        if local_tail < 0:
            raise ValueError("tail notification requires a non-negative position")
        condition = self._condition(segment_id)
        async with condition:
            while True:
                state = await self._store.state(segment_id, known_tail)
                if state.local_tail >= local_tail or state.sealed:
                    return state
                await condition.wait()
