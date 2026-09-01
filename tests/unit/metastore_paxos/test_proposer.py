import pytest

from delos_lab.metastore.paxos.acceptor import PaxosAcceptor
from delos_lab.metastore.paxos.errors import PaxosNoQuorum
from delos_lab.metastore.paxos.proposer import PaxosProposer
from delos_lab.metastore.paxos.state_machine import VersionRegisterStateMachine
from delos_lab.metastore.paxos.storage import MemoryPaxosStorage
from delos_lab.metastore.paxos.transport import DirectPaxosTransport
from delos_lab.metastore.paxos.types import (
    AcceptRequest,
    Ballot,
    CompareAndSetCommand,
    PaxosValue,
    PrepareRequest,
)
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.virtual_log.types import LogChain, LogSegment

MEMBERS = ("meta-1", "meta-2", "meta-3")
MEMBERS_5 = ("meta-1", "meta-2", "meta-3", "meta-4", "meta-5")


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


def paxos_value(
    segment_id: str,
    expected_version: int = 0,
    members: tuple[str, ...] = MEMBERS,
) -> PaxosValue:
    return PaxosValue(
        command=CompareAndSetCommand(
            expected_version=expected_version,
            new_chain=chain(segment_id),
        ),
    )


async def cluster(
    members: tuple[str, ...] = MEMBERS,
) -> tuple[
    dict[str, PaxosAcceptor],
    DirectPaxosTransport,
]:
    transport = DirectPaxosTransport()
    acceptors: dict[str, PaxosAcceptor] = {}
    for node_id in members:
        acceptor = await PaxosAcceptor.create(
            node_id,
            MemoryPaxosStorage(),
            VersionRegisterStateMachine(),
        )
        acceptors[node_id] = acceptor
        transport.register(node_id, acceptor)
    return acceptors, transport


async def test_five_member_proposer_decides_with_two_peers_down() -> None:
    acceptors, transport = await cluster(MEMBERS_5)
    transport.unavailable.update({("meta-1", "meta-4"), ("meta-1", "meta-5")})
    proposer = PaxosProposer("meta-1", MEMBERS_5, acceptors["meta-1"], transport)

    slot, _ = await proposer.propose(paxos_value("segment-five", members=MEMBERS_5))

    assert slot == 1
    assert sum(acceptor.state.last_applied == 1 for acceptor in acceptors.values()) == 3


async def test_proposer_decides_with_one_peer_down() -> None:
    acceptors, transport = await cluster()
    transport.unavailable.add(("meta-1", "meta-3"))
    proposer = PaxosProposer("meta-1", MEMBERS, acceptors["meta-1"], transport)
    value = paxos_value("segment-a")

    slot, decided = await proposer.propose(value)

    assert slot == 1
    assert decided == value
    assert acceptors["meta-1"].state.last_applied == 1
    assert acceptors["meta-2"].state.last_applied == 1
    assert acceptors["meta-3"].state.last_applied == 0


async def test_proposer_fails_without_a_quorum() -> None:
    acceptors, transport = await cluster()
    transport.unavailable.update({("meta-1", "meta-2"), ("meta-1", "meta-3")})
    proposer = PaxosProposer("meta-1", MEMBERS, acceptors["meta-1"], transport)

    with pytest.raises(PaxosNoQuorum):
        await proposer.propose(paxos_value("segment-a"))

    assert acceptors["meta-1"].state.last_applied == 0


async def test_proposer_recovers_accepted_value_before_its_own_value() -> None:
    acceptors, transport = await cluster()
    old = paxos_value("segment-a")
    ballot = Ballot(round=4, node_id="meta-2")
    for member in ("meta-2", "meta-3"):
        await acceptors[member].prepare(PrepareRequest(slot=1, ballot=ballot))
        await acceptors[member].accept(AcceptRequest(slot=1, ballot=ballot, value=old))
    proposer = PaxosProposer("meta-1", MEMBERS, acceptors["meta-1"], transport)

    slot, decided = await proposer.propose(paxos_value("segment-b"))

    assert slot == 2
    assert isinstance(decided.command, CompareAndSetCommand)
    assert decided.command.new_chain.active.segment_id == "segment-b"
    assert all(acceptor.state.slots[0].decided_value == old for acceptor in acceptors.values())


async def test_each_operation_reserves_a_fresh_ballot() -> None:
    acceptors, transport = await cluster()
    proposer = PaxosProposer("meta-1", MEMBERS, acceptors["meta-1"], transport)

    await proposer.propose(paxos_value("segment-a"))
    await proposer.propose(paxos_value("segment-b", expected_version=1))

    assert acceptors["meta-1"].state.local_round == 2
    assert acceptors["meta-1"].state.last_applied == 2
