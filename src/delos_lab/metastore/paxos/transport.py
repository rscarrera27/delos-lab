from typing import Protocol

from .acceptor import PaxosAcceptor
from .types import (
    AcceptRequest,
    AcceptResponse,
    DecideRequest,
    DecideResponse,
    PrepareRequest,
    PrepareResponse,
)


class PaxosTransport(Protocol):
    async def prepare(
        self,
        source: str,
        target: str,
        request: PrepareRequest,
    ) -> PrepareResponse: ...

    async def accept(
        self,
        source: str,
        target: str,
        request: AcceptRequest,
    ) -> AcceptResponse: ...

    async def decide(
        self,
        source: str,
        target: str,
        request: DecideRequest,
    ) -> DecideResponse: ...


class DirectPaxosTransport:
    def __init__(self) -> None:
        self._acceptors: dict[str, PaxosAcceptor] = {}
        self.unavailable: set[tuple[str, str]] = set()

    def register(self, node_id: str, acceptor: PaxosAcceptor) -> None:
        self._acceptors[node_id] = acceptor

    def _check(self, source: str, target: str) -> None:
        if (source, target) in self.unavailable:
            raise ConnectionError(f"{source}->{target}")
        if target not in self._acceptors:
            raise ConnectionError(f"unknown Paxos peer: {target}")

    async def prepare(
        self,
        source: str,
        target: str,
        request: PrepareRequest,
    ) -> PrepareResponse:
        self._check(source, target)
        return await self._acceptors[target].prepare(request)

    async def accept(
        self,
        source: str,
        target: str,
        request: AcceptRequest,
    ) -> AcceptResponse:
        self._check(source, target)
        return await self._acceptors[target].accept(request)

    async def decide(
        self,
        source: str,
        target: str,
        request: DecideRequest,
    ) -> DecideResponse:
        self._check(source, target)
        return await self._acceptors[target].decide(request)
