from typing import Protocol

from .types import PersistentPaxosState


class PaxosStorage(Protocol):
    async def load(self) -> PersistentPaxosState: ...

    async def save(self, state: PersistentPaxosState) -> None: ...


class MemoryPaxosStorage:
    def __init__(self, state: PersistentPaxosState | None = None) -> None:
        self._state = state or PersistentPaxosState()

    async def load(self) -> PersistentPaxosState:
        return self._state

    async def save(self, state: PersistentPaxosState) -> None:
        self._state = state
