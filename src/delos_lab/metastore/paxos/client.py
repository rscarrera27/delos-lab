from collections.abc import Mapping
from typing import Protocol

from delos_lab.common.membership import validate_fixed_members
from delos_lab.virtual_log.metastore import (
    Applied,
    CompareAndSetResult,
    VersionMismatch,
)
from delos_lab.virtual_log.types import LogChain, VersionedLogChain

from .acceptor import PaxosAcceptor
from .errors import PaxosNoQuorum
from .proposer import PaxosProposer
from .types import (
    CompareAndSetCommand,
    PaxosValue,
    ReadBarrierCommand,
)


class MetaStorePeer(Protocol):
    async def read(self) -> VersionedLogChain: ...

    async def compare_and_set(
        self,
        expected_version: int,
        new_chain: LogChain,
    ) -> CompareAndSetResult: ...


class PaxosMetaStore:
    def __init__(
        self,
        proposer: PaxosProposer,
        acceptor: PaxosAcceptor,
        members: tuple[str, ...],
    ) -> None:
        self._proposer = proposer
        self._acceptor = acceptor
        validate_fixed_members(members, label="Paxos")

    async def read(self) -> VersionedLogChain:
        slot, _ = await self._proposer.propose(PaxosValue(command=ReadBarrierCommand()))
        result = self._acceptor.result(slot)
        if not isinstance(result, VersionedLogChain):
            raise TypeError("read barrier returned a non-snapshot result")
        return result

    async def compare_and_set(
        self,
        expected_version: int,
        new_chain: LogChain,
    ) -> CompareAndSetResult:
        slot, _ = await self._proposer.propose(
            PaxosValue(
                command=CompareAndSetCommand(
                    expected_version=expected_version,
                    new_chain=new_chain,
                )
            )
        )
        result = self._acceptor.result(slot)
        if not isinstance(result, (Applied, VersionMismatch)):
            raise TypeError("compare-and-set returned a non-CAS result")
        return result


class PaxosMetaStoreClient:
    def __init__(self, peers: Mapping[str, MetaStorePeer]) -> None:
        if not peers:
            raise ValueError("at least one MetaStore peer is required")
        self._peers = dict(peers)
        self.preferred_peer: str | None = None

    def _candidates(self) -> list[str]:
        candidates = list(self._peers)
        if self.preferred_peer in self._peers:
            candidates.remove(self.preferred_peer)
            candidates.insert(0, self.preferred_peer)
        return candidates

    async def read(self) -> VersionedLogChain:
        for node_id in self._candidates():
            try:
                result = await self._peers[node_id].read()
            except ConnectionError, PaxosNoQuorum:
                continue
            self.preferred_peer = node_id
            return result
        raise PaxosNoQuorum("no MetaStore peer completed the read")

    async def compare_and_set(
        self,
        expected_version: int,
        new_chain: LogChain,
    ) -> CompareAndSetResult:
        for node_id in self._candidates():
            try:
                result = await self._peers[node_id].compare_and_set(
                    expected_version,
                    new_chain,
                )
            except ConnectionError, PaxosNoQuorum:
                continue
            self.preferred_peer = node_id
            return result
        raise PaxosNoQuorum("no MetaStore peer completed compare-and-set")
