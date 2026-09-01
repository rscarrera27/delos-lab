import asyncio

from delos_lab.virtual_log.metastore import Applied, CompareAndSetResult, VersionMismatch
from delos_lab.virtual_log.types import LogChain, VersionedLogChain


class MemoryMetaStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._snapshot = VersionedLogChain(version=0, chain=None)

    async def read(self) -> VersionedLogChain:
        async with self._lock:
            return self._snapshot

    async def compare_and_set(
        self, expected_version: int, new_chain: LogChain
    ) -> CompareAndSetResult:
        async with self._lock:
            if self._snapshot.version != expected_version:
                return VersionMismatch(current=self._snapshot)

            self._snapshot = VersionedLogChain(
                version=expected_version + 1,
                chain=new_chain,
            )
            return Applied(snapshot=self._snapshot)
