import pytest
from hypothesis import given
from hypothesis import strategies as st

from delos_lab.metastore.paxos.acceptor import PaxosAcceptor
from delos_lab.metastore.paxos.errors import PaxosSafetyError
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


def value(segment_id: str) -> PaxosValue:
    return PaxosValue(
        command=CompareAndSetCommand(
            expected_version=0,
            new_chain=LogChain(
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
            ),
        ),
    )


async def acceptor(node_id: str = "meta-1") -> PaxosAcceptor:
    return await PaxosAcceptor.create(
        node_id,
        MemoryPaxosStorage(),
        VersionRegisterStateMachine(),
    )


async def cluster() -> tuple[dict[str, PaxosAcceptor], DirectPaxosTransport]:
    transport = DirectPaxosTransport()
    acceptors = {member: await acceptor(member) for member in MEMBERS}
    for member, peer in acceptors.items():
        transport.register(member, peer)
    return acceptors, transport


async def test_same_ballot_cannot_accept_two_different_values() -> None:
    peer = await acceptor()
    ballot = Ballot(round=1, node_id="meta-1")
    await peer.accept(AcceptRequest(slot=1, ballot=ballot, value=value("left")))

    with pytest.raises(PaxosSafetyError):
        await peer.accept(AcceptRequest(slot=1, ballot=ballot, value=value("right")))


@given(
    first_round=st.integers(min_value=1, max_value=30),
    second_round=st.integers(min_value=1, max_value=30),
    first_node=st.sampled_from(MEMBERS),
    second_node=st.sampled_from(MEMBERS),
)
async def test_acceptor_never_accepts_below_its_highest_promise(
    first_round: int,
    second_round: int,
    first_node: str,
    second_node: str,
) -> None:
    peer = await acceptor()
    first = Ballot(round=first_round, node_id=first_node)
    second = Ballot(round=second_round, node_id=second_node)
    high, low = max(first, second), min(first, second)
    await peer.prepare(PrepareRequest(slot=1, ballot=high))

    response = await peer.accept(AcceptRequest(slot=1, ballot=low, value=value("segment-a")))

    assert response.accepted is (low == high)


@given(
    chosen_round=st.integers(min_value=1, max_value=20),
    proposer=st.sampled_from(MEMBERS),
)
async def test_a_chosen_value_is_recovered_before_a_new_proposal(
    chosen_round: int,
    proposer: str,
) -> None:
    acceptors, transport = await cluster()
    chosen = value("chosen")
    chosen_ballot = Ballot(round=chosen_round, node_id="meta-3")
    for member in ("meta-2", "meta-3"):
        await acceptors[member].accept(AcceptRequest(slot=1, ballot=chosen_ballot, value=chosen))
    proposer_instance = PaxosProposer(
        proposer,
        MEMBERS,
        acceptors[proposer],
        transport,
    )

    slot, decided = await proposer_instance.propose(value("new"))

    assert slot == 2
    assert decided == value("new")
    assert all(peer.state.slots[0].decided_value == chosen for peer in acceptors.values())
