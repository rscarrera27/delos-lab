from collections.abc import Mapping
from typing import Protocol

from .server import NativeLogServer
from .store import LogletStore
from .types import LogEntry, LogServerState


class LogletTransport(Protocol):
    async def put(self, node_id: str, entry: LogEntry, known_tail: int = 0) -> LogServerState: ...

    async def repair(
        self, node_id: str, entry: LogEntry, known_tail: int = 0
    ) -> LogServerState: ...

    async def get(
        self, node_id: str, segment_id: str, position: int, known_tail: int = 0
    ) -> LogEntry | None: ...

    async def seal(self, node_id: str, segment_id: str, known_tail: int = 0) -> LogServerState: ...

    async def prefix_trim(
        self, node_id: str, segment_id: str, trim_position: int
    ) -> LogServerState: ...

    async def state(self, node_id: str, segment_id: str, known_tail: int = 0) -> LogServerState: ...

    async def wait_for_tail(
        self,
        node_id: str,
        segment_id: str,
        local_tail: int,
        known_tail: int = 0,
    ) -> LogServerState: ...


class DirectLogletTransport:
    def __init__(self, stores: Mapping[str, LogletStore]) -> None:
        self._servers = {node_id: NativeLogServer(store) for node_id, store in stores.items()}
        self.unavailable: set[str] = set()

    def _ensure_available(self, node_id: str) -> None:
        if node_id in self.unavailable:
            raise ConnectionError(node_id)

    async def put(self, node_id: str, entry: LogEntry, known_tail: int = 0) -> LogServerState:
        self._ensure_available(node_id)
        return await self._servers[node_id].put(entry, known_tail)

    async def repair(self, node_id: str, entry: LogEntry, known_tail: int = 0) -> LogServerState:
        self._ensure_available(node_id)
        return await self._servers[node_id].repair(entry, known_tail)

    async def get(
        self, node_id: str, segment_id: str, position: int, known_tail: int = 0
    ) -> LogEntry | None:
        self._ensure_available(node_id)
        return await self._servers[node_id].get(segment_id, position, known_tail)

    async def seal(self, node_id: str, segment_id: str, known_tail: int = 0) -> LogServerState:
        self._ensure_available(node_id)
        return await self._servers[node_id].seal(segment_id, known_tail)

    async def prefix_trim(
        self, node_id: str, segment_id: str, trim_position: int
    ) -> LogServerState:
        self._ensure_available(node_id)
        return await self._servers[node_id].prefix_trim(segment_id, trim_position)

    async def state(self, node_id: str, segment_id: str, known_tail: int = 0) -> LogServerState:
        self._ensure_available(node_id)
        return await self._servers[node_id].state(segment_id, known_tail)

    async def wait_for_tail(
        self,
        node_id: str,
        segment_id: str,
        local_tail: int,
        known_tail: int = 0,
    ) -> LogServerState:
        self._ensure_available(node_id)
        return await self._servers[node_id].wait_for_tail(
            segment_id,
            local_tail,
            known_tail,
        )
