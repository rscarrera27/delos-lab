from delos_lab.metastore.paxos.state_machine import VersionRegisterStateMachine
from delos_lab.metastore.paxos.types import CompareAndSetCommand, ReadBarrierCommand
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.virtual_log.metastore import Applied, VersionMismatch
from delos_lab.virtual_log.types import LogChain, LogSegment, VersionedLogChain


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


def test_cas_barrier_and_mismatch_apply_deterministically() -> None:
    machine = VersionRegisterStateMachine()
    first = chain("segment-a")

    applied = machine.apply(CompareAndSetCommand(expected_version=0, new_chain=first))
    observed = machine.apply(ReadBarrierCommand())
    mismatch = machine.apply(CompareAndSetCommand(expected_version=0, new_chain=chain("segment-b")))

    assert isinstance(applied, Applied)
    assert observed == applied.snapshot
    assert isinstance(mismatch, VersionMismatch)
    assert mismatch.current == observed


def test_restore_replaces_the_materialized_snapshot() -> None:
    machine = VersionRegisterStateMachine()
    snapshot = VersionedLogChain(version=1, chain=chain("segment-a"))

    machine.restore(snapshot)

    assert machine.snapshot == snapshot
    assert machine.apply(ReadBarrierCommand()) == snapshot
