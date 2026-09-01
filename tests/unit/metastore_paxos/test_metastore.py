import asyncio

import pytest

from delos_lab.metastore.paxos.acceptor import PaxosAcceptor
from delos_lab.metastore.paxos.client import PaxosMetaStore, PaxosMetaStoreClient
from delos_lab.metastore.paxos.errors import PaxosNoQuorum
from delos_lab.metastore.paxos.proposer import PaxosProposer
from delos_lab.metastore.paxos.state_machine import VersionRegisterStateMachine
from delos_lab.metastore.paxos.storage import MemoryPaxosStorage
from delos_lab.metastore.paxos.transport import DirectPaxosTransport
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.virtual_log.metastore import Applied, VersionMismatch
from delos_lab.virtual_log.types import LogChain, LogSegment, VersionedLogChain

MEMBERS = ("meta-1", "meta-2", "meta-3")


def chain(segment_id: str) -> LogChain:
    return LogChain(
        segments=(
            LogSegment(
                segment_id=segment_id,
                virtual_start=0,
                virtual_stop=None,
                loglet=native_loglet_configuration(
                    ("db-1", "db-2", "db-3"),
                    "db-1",
                    f"inc-{segment_id}",
                ),
            ),
        )
    )


async def metastore_cluster() -> tuple[
    dict[str, PaxosMetaStore],
    DirectPaxosTransport,
]:
    transport = DirectPaxosTransport()
    acceptors: dict[str, PaxosAcceptor] = {}
    for node_id in MEMBERS:
        acceptor = await PaxosAcceptor.create(
            node_id,
            MemoryPaxosStorage(),
            VersionRegisterStateMachine(),
        )
        acceptors[node_id] = acceptor
        transport.register(node_id, acceptor)
    stores = {
        node_id: PaxosMetaStore(
            PaxosProposer(node_id, MEMBERS, acceptor, transport),
            acceptor,
            MEMBERS,
        )
        for node_id, acceptor in acceptors.items()
    }
    return stores, transport


async def test_cas_and_barrier_read_match_metastore_contract() -> None:
    stores, _ = await metastore_cluster()
    first = chain("segment-a")

    applied = await stores["meta-1"].compare_and_set(0, first)
    mismatch = await stores["meta-2"].compare_and_set(0, chain("segment-b"))
    observed = await stores["meta-3"].read()

    assert isinstance(applied, Applied)
    assert applied.snapshot == VersionedLogChain(version=1, chain=first)
    assert isinstance(mismatch, VersionMismatch)
    assert mismatch.current == applied.snapshot
    assert observed == applied.snapshot


async def test_concurrent_cas_has_one_winner() -> None:
    stores, _ = await metastore_cluster()

    left, right = await asyncio.gather(
        stores["meta-1"].compare_and_set(0, chain("left")),
        stores["meta-2"].compare_and_set(0, chain("right")),
    )

    assert sum(isinstance(result, Applied) for result in (left, right)) == 1
    assert sum(isinstance(result, VersionMismatch) for result in (left, right)) == 1
    snapshots = await asyncio.gather(*(store.read() for store in stores.values()))
    assert len({snapshot.model_dump_json() for snapshot in snapshots}) == 1


async def test_metastore_fails_when_two_peer_links_are_down() -> None:
    stores, transport = await metastore_cluster()
    transport.unavailable.update({("meta-1", "meta-2"), ("meta-1", "meta-3")})

    with pytest.raises(PaxosNoQuorum):
        await stores["meta-1"].read()


class UnavailablePeer:
    async def read(self) -> VersionedLogChain:
        raise ConnectionError("peer unavailable")

    async def compare_and_set(
        self,
        expected_version: int,
        new_chain: LogChain,
    ) -> Applied | VersionMismatch:
        del expected_version, new_chain
        raise PaxosNoQuorum("peer cannot reach acceptors")


async def test_client_reports_no_quorum_after_every_peer_fails() -> None:
    client = PaxosMetaStoreClient({node_id: UnavailablePeer() for node_id in MEMBERS})

    with pytest.raises(PaxosNoQuorum):
        await client.read()
    with pytest.raises(PaxosNoQuorum):
        await client.compare_and_set(0, chain("segment-a"))
