from pathlib import Path

from delos_lab.metastore.paxos.acceptor import PaxosAcceptor
from delos_lab.metastore.paxos.sqlite_storage import SQLitePaxosStorage
from delos_lab.metastore.paxos.state_machine import VersionRegisterStateMachine
from delos_lab.metastore.paxos.types import (
    AcceptorSlotState,
    Ballot,
    CompareAndSetCommand,
    DecideRequest,
    PaxosValue,
    PersistentPaxosState,
    PrepareRequest,
)
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.virtual_log.types import LogChain, LogSegment, VersionedLogChain

MEMBERS = ("meta-1", "meta-2", "meta-3")


def chain() -> LogChain:
    return LogChain(
        segments=(
            LogSegment(
                segment_id="segment-a",
                virtual_start=0,
                virtual_stop=None,
                loglet=native_loglet_configuration(
                    ("db-1", "db-2", "db-3"),
                    "db-1",
                    "inc-a",
                ),
            ),
        )
    )


def value() -> PaxosValue:
    return PaxosValue(
        command=CompareAndSetCommand(expected_version=0, new_chain=chain()),
    )


def decided_state() -> PersistentPaxosState:
    ballot = Ballot(round=9, node_id="meta-1")
    return PersistentPaxosState(
        local_round=9,
        slots=(
            AcceptorSlotState(
                slot=1,
                promised_ballot=ballot,
                accepted_ballot=ballot,
                accepted_value=value(),
                decided_value=value(),
            ),
        ),
        last_applied=1,
        state_machine=VersionedLogChain(version=1, chain=chain()),
    )


async def test_sqlite_restores_round_slots_and_register(tmp_path: Path) -> None:
    path = tmp_path / "meta-1.sqlite3"
    storage = SQLitePaxosStorage(path)
    await storage.open()
    state = decided_state()
    await storage.save(state)
    assert await storage.journal_mode() == "wal"
    await storage.close()

    reopened = SQLitePaxosStorage(path)
    await reopened.open()
    try:
        assert await reopened.load() == state
    finally:
        await reopened.close()


async def test_restarted_acceptor_preserves_its_promise(tmp_path: Path) -> None:
    path = tmp_path / "promise.sqlite3"
    storage = SQLitePaxosStorage(path)
    await storage.open()
    acceptor = await PaxosAcceptor.create("meta-1", storage, VersionRegisterStateMachine())
    high = Ballot(round=5, node_id="meta-2")
    await acceptor.prepare(PrepareRequest(slot=1, ballot=high))
    await storage.close()

    reopened = SQLitePaxosStorage(path)
    await reopened.open()
    restored = await PaxosAcceptor.create("meta-1", reopened, VersionRegisterStateMachine())
    try:
        response = await restored.prepare(
            PrepareRequest(slot=1, ballot=Ballot(round=4, node_id="meta-3"))
        )
        assert response.promised is False
        assert response.promised_ballot == high
    finally:
        await reopened.close()


async def test_restarted_acceptor_restores_applied_register(tmp_path: Path) -> None:
    path = tmp_path / "decision.sqlite3"
    storage = SQLitePaxosStorage(path)
    await storage.open()
    acceptor = await PaxosAcceptor.create("meta-1", storage, VersionRegisterStateMachine())
    await acceptor.decide(DecideRequest(slot=1, value=value()))
    await storage.close()

    reopened = SQLitePaxosStorage(path)
    await reopened.open()
    restored = await PaxosAcceptor.create("meta-1", reopened, VersionRegisterStateMachine())
    try:
        assert restored.state.last_applied == 1
        assert restored.state.state_machine == VersionedLogChain(
            version=1,
            chain=chain(),
        )
    finally:
        await reopened.close()
