import pytest

from delos_lab.metastore.paxos.acceptor import PaxosAcceptor
from delos_lab.metastore.paxos.errors import PaxosSafetyError
from delos_lab.metastore.paxos.state_machine import VersionRegisterStateMachine
from delos_lab.metastore.paxos.storage import MemoryPaxosStorage
from delos_lab.metastore.paxos.types import (
    AcceptRequest,
    Ballot,
    CompareAndSetCommand,
    DecideRequest,
    PaxosValue,
    PrepareRequest,
    ReadBarrierCommand,
)
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.virtual_log.metastore import Applied
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


def cas_value(segment_id: str) -> PaxosValue:
    return PaxosValue(
        command=CompareAndSetCommand(
            expected_version=0,
            new_chain=chain(segment_id),
        ),
    )


def barrier_value() -> PaxosValue:
    return PaxosValue(command=ReadBarrierCommand())


async def memory_acceptor() -> tuple[PaxosAcceptor, MemoryPaxosStorage]:
    storage = MemoryPaxosStorage()
    acceptor = await PaxosAcceptor.create(
        "meta-1",
        storage,
        VersionRegisterStateMachine(),
    )
    return acceptor, storage


async def test_prepare_persists_promise_and_rejects_lower_ballot() -> None:
    acceptor, storage = await memory_acceptor()
    high = Ballot(round=2, node_id="meta-1")

    granted = await acceptor.prepare(PrepareRequest(slot=1, ballot=high))
    rejected = await acceptor.prepare(
        PrepareRequest(slot=1, ballot=Ballot(round=1, node_id="meta-3"))
    )

    assert granted.promised is True
    assert rejected.promised is False
    assert rejected.promised_ballot == high
    assert (await storage.load()).slots[0].promised_ballot == high


async def test_prepare_reports_an_existing_accepted_value() -> None:
    acceptor, _ = await memory_acceptor()
    first = Ballot(round=1, node_id="meta-1")
    second = Ballot(round=2, node_id="meta-2")
    value = cas_value("segment-a")
    await acceptor.accept(AcceptRequest(slot=1, ballot=first, value=value))

    response = await acceptor.prepare(PrepareRequest(slot=1, ballot=second))

    assert response.promised is True
    assert response.accepted_ballot == first
    assert response.accepted_value == value


async def test_accept_rejects_below_promise_and_persists_success() -> None:
    acceptor, storage = await memory_acceptor()
    value = cas_value("segment-a")
    ballot = Ballot(round=2, node_id="meta-1")
    await acceptor.prepare(PrepareRequest(slot=1, ballot=ballot))

    rejected = await acceptor.accept(
        AcceptRequest(
            slot=1,
            ballot=Ballot(round=1, node_id="meta-3"),
            value=value,
        )
    )
    accepted = await acceptor.accept(AcceptRequest(slot=1, ballot=ballot, value=value))

    assert rejected.accepted is False
    assert accepted.accepted is True
    assert (await storage.load()).slots[0].accepted_value == value


async def test_out_of_order_decisions_wait_for_the_gap() -> None:
    acceptor, storage = await memory_acceptor()
    await acceptor.decide(DecideRequest(slot=2, value=barrier_value()))
    assert (await storage.load()).last_applied == 0

    await acceptor.decide(DecideRequest(slot=1, value=cas_value("segment-a")))

    state = await storage.load()
    assert state.last_applied == 2
    assert state.state_machine == VersionedLogChain(
        version=1,
        chain=chain("segment-a"),
    )
    assert isinstance(acceptor.result(1), Applied)
    assert acceptor.result(2) == state.state_machine


async def test_repeated_decision_is_idempotent() -> None:
    acceptor, _ = await memory_acceptor()
    request = DecideRequest(slot=1, value=cas_value("segment-a"))

    first = await acceptor.decide(request)
    second = await acceptor.decide(request)

    assert first.learned is True
    assert second.learned is True
    assert acceptor.state.last_applied == 1


async def test_conflicting_decision_raises_a_safety_error() -> None:
    acceptor, _ = await memory_acceptor()
    await acceptor.decide(DecideRequest(slot=1, value=cas_value("segment-a")))

    with pytest.raises(PaxosSafetyError):
        await acceptor.decide(DecideRequest(slot=1, value=cas_value("segment-b")))


async def test_ballot_round_is_reserved_durably() -> None:
    acceptor, storage = await memory_acceptor()

    first = await acceptor.reserve_ballot()
    after_observation = await acceptor.reserve_ballot(minimum_round=7)

    assert first == Ballot(round=1, node_id="meta-1")
    assert after_observation == Ballot(round=8, node_id="meta-1")
    assert (await storage.load()).local_round == 8
