from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .types import LogChain, VersionedLogChain


class Applied(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot: VersionedLogChain


class VersionMismatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    current: VersionedLogChain


type CompareAndSetResult = Applied | VersionMismatch


class MetaStore(Protocol):
    """Versioned-register port required by the VirtualLog control plane."""

    async def read(self) -> VersionedLogChain: ...

    async def compare_and_set(
        self, expected_version: int, new_chain: LogChain
    ) -> CompareAndSetResult: ...
