from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .types import LogSegment


class LogletUnavailable(Exception):
    """No runtime adapter is available for the segment configuration."""


class LogletSealed(Exception):
    """The selected Loglet no longer accepts appends."""


class LogletAppend(BaseModel):
    model_config = ConfigDict(frozen=True)

    position: int = Field(ge=0)
    known_tail: int = Field(ge=1)


class LogletTail(BaseModel):
    model_config = ConfigDict(frozen=True)

    tail: int = Field(ge=0)
    sealed: bool


class LogletEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    position: int = Field(ge=0)
    command_id: str
    payload: bytes


class VirtualLoglet(Protocol):
    """Paper section 3.4 data-plane contract.

    Positions are local to one Loglet. Implementations may be sparse; only a
    concrete Loglet may strengthen that contract to a dense global prefix.
    """

    async def append(self, command_id: str, payload: bytes) -> LogletAppend: ...

    async def seal(self) -> None: ...

    async def check_tail(self) -> LogletTail: ...

    async def read_next(self, local_start: int, local_stop: int) -> LogletEntry | None:
        """Return the first entry in the half-open local range, or ``None``."""
        ...

    async def prefix_trim(self, trim_position: int) -> int:
        """Discard positions below ``trim_position`` and return the trim watermark."""
        ...


class LogletProvider(Protocol):
    """Resolve an opaque Loglet configuration without coupling VirtualLog to it."""

    def get(self, segment: LogSegment) -> VirtualLoglet: ...


class StaticLogletProvider:
    """Small adapter registry used by tests and in-process demonstrations."""

    def __init__(self, runtimes: Mapping[str, VirtualLoglet]) -> None:
        self._runtimes = dict(runtimes)

    def get(self, segment: LogSegment) -> VirtualLoglet:
        try:
            return self._runtimes[segment.segment_id]
        except KeyError as error:
            raise LogletUnavailable(segment.segment_id) from error
