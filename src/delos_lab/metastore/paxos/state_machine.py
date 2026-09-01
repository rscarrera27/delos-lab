from delos_lab.virtual_log.metastore import Applied, CompareAndSetResult, VersionMismatch
from delos_lab.virtual_log.types import VersionedLogChain

from .types import CompareAndSetCommand, PaxosCommand, ReadBarrierCommand

type StateMachineResult = CompareAndSetResult | VersionedLogChain


class VersionRegisterStateMachine:
    def __init__(self) -> None:
        self._snapshot = VersionedLogChain(version=0, chain=None)

    @property
    def snapshot(self) -> VersionedLogChain:
        return self._snapshot

    def restore(self, snapshot: VersionedLogChain) -> None:
        self._snapshot = snapshot

    def apply(self, command: PaxosCommand) -> StateMachineResult:
        if isinstance(command, ReadBarrierCommand):
            return self._snapshot
        if not isinstance(command, CompareAndSetCommand):
            raise TypeError("unknown Paxos command")
        if self._snapshot.version != command.expected_version:
            return VersionMismatch(current=self._snapshot)
        self._snapshot = VersionedLogChain(
            version=command.expected_version + 1,
            chain=command.new_chain,
        )
        return Applied(snapshot=self._snapshot)
