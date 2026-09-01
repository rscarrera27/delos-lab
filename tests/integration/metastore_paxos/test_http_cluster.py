import httpx

from delos_lab.metastore.paxos.acceptor import PaxosAcceptor
from delos_lab.metastore.paxos.client import PaxosMetaStore
from delos_lab.metastore.paxos.http_api import create_paxos_app
from delos_lab.metastore.paxos.http_transport import HttpMetaStorePeer, HttpPaxosTransport
from delos_lab.metastore.paxos.proposer import PaxosProposer
from delos_lab.metastore.paxos.state_machine import VersionRegisterStateMachine
from delos_lab.metastore.paxos.storage import MemoryPaxosStorage
from delos_lab.metastore.paxos.transport import DirectPaxosTransport
from delos_lab.metastore.paxos.types import (
    AcceptRequest,
    Ballot,
    CompareAndSetCommand,
    DecideRequest,
    PaxosValue,
    PrepareRequest,
)
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.virtual_log.metastore import Applied
from delos_lab.virtual_log.types import LogChain, LogSegment

MEMBERS = ("meta-1", "meta-2", "meta-3")


def chain(segment_id: str = "segment-a") -> LogChain:
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


def value() -> PaxosValue:
    return PaxosValue(
        command=CompareAndSetCommand(expected_version=0, new_chain=chain()),
    )


async def acceptor(node_id: str = "meta-1") -> PaxosAcceptor:
    return await PaxosAcceptor.create(
        node_id,
        MemoryPaxosStorage(),
        VersionRegisterStateMachine(),
    )


async def public_cluster() -> tuple[
    PaxosMetaStore,
    DirectPaxosTransport,
    PaxosAcceptor,
]:
    transport = DirectPaxosTransport()
    peers = {member: await acceptor(member) for member in MEMBERS}
    for member, peer in peers.items():
        transport.register(member, peer)
    store = PaxosMetaStore(
        PaxosProposer("meta-1", MEMBERS, peers["meta-1"], transport),
        peers["meta-1"],
        MEMBERS,
    )
    return store, transport, peers["meta-1"]


async def test_http_round_trips_prepare_accept_and_decide() -> None:
    peer = await acceptor()
    app = create_paxos_app("meta-1", peer, metastore=None)
    proposed = value()
    ballot = Ballot(round=1, node_id="meta-2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://meta-1",
    ) as client:
        transport = HttpPaxosTransport({"meta-1": "http://meta-1"}, client)
        prepared = await transport.prepare(
            "meta-2", "meta-1", PrepareRequest(slot=1, ballot=ballot)
        )
        accepted = await transport.accept(
            "meta-2",
            "meta-1",
            AcceptRequest(slot=1, ballot=ballot, value=proposed),
        )
        learned = await transport.decide("meta-2", "meta-1", DecideRequest(slot=1, value=proposed))

    assert prepared.promised is True
    assert accepted.accepted is True
    assert learned.learned is True
    assert peer.state.last_applied == 1


async def test_public_api_serves_cas_and_barrier_read() -> None:
    store, _, local_acceptor = await public_cluster()
    app = create_paxos_app("meta-1", local_acceptor, store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://meta-1",
    ) as client:
        applied = await client.post(
            "/metastore/compare-and-set",
            json={
                "expected_version": 0,
                "new_chain": chain().model_dump(mode="json"),
            },
        )
        observed = await client.get("/metastore")

    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert applied.json()["snapshot"]["version"] == 1
    assert observed.status_code == 200
    assert observed.json()["version"] == 1


async def test_public_api_returns_no_quorum() -> None:
    store, transport, local_acceptor = await public_cluster()
    transport.unavailable.update({("meta-1", "meta-2"), ("meta-1", "meta-3")})
    app = create_paxos_app("meta-1", local_acceptor, store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://meta-1",
    ) as client:
        response = await client.get("/metastore")

    assert response.status_code == 503
    assert response.json() == {"code": "PAXOS_NO_QUORUM"}


async def test_http_metastore_peer_restores_public_results() -> None:
    store, _, local_acceptor = await public_cluster()
    app = create_paxos_app("meta-1", local_acceptor, store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://meta-1",
    ) as client:
        peer = HttpMetaStorePeer("http://meta-1", client)
        applied = await peer.compare_and_set(0, chain())
        observed = await peer.read()

    assert isinstance(applied, Applied)
    assert applied.snapshot == observed
