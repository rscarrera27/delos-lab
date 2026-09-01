from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.native_loglet.types import AppendResult, CheckTailResult, LogEntry, LogServerState


class NativeLogletScenario:
    """Test-only driver whose verbs match the NativeLoglet paper contract.

    The driver hides repetitive in-memory wiring, not protocol outcomes. Tests
    still invoke operations explicitly and make ordinary pytest assertions.
    ``write_local_copy`` deliberately bypasses the sequencer so a scenario can
    construct partial, zombie, and repair states without calling them committed.
    """

    members = ("db-1", "db-2", "db-3")

    def __init__(
        self,
        *,
        segment_id: str = "segment-a",
        sequencer_id: str = "db-1",
        retry_interval: float = 0.01,
    ) -> None:
        self.segment_id = segment_id
        self.stores = {node: MemoryLogletStore(node) for node in self.members}
        self.transport = DirectLogletTransport(self.stores)
        self.sequencer = NativeSequencer(
            segment_id,
            sequencer_id,
            self.members,
            self.transport,
            retry_interval=retry_interval,
            max_retry_interval=max(retry_interval, 0.01),
        )
        self.client = NativeLogletClient(
            segment_id,
            self.members,
            self.transport,
        )

    async def append(self, command_id: str, payload: bytes) -> AppendResult:
        """Append through the sequencer; a successful return means global commit."""
        return await self.sequencer.append(command_id, payload)

    async def write_local_copy(
        self,
        node_id: str,
        position: int,
        *,
        command_id: str | None = None,
        payload: bytes = b"value",
    ) -> LogEntry:
        """Place one physical copy without claiming global commitment."""
        entry = LogEntry(
            segment_id=self.segment_id,
            position=position,
            command_id=command_id or f"command-{position}",
            payload=payload,
        )
        await self.transport.repair(node_id, entry)
        return entry

    async def seal(self, *node_ids: str, known_tail: int = 0) -> None:
        """Set the seal bit on the selected LogServers only."""
        for node_id in node_ids:
            await self.transport.seal(node_id, self.segment_id, known_tail)

    def disconnect(self, *node_ids: str) -> None:
        self.transport.unavailable.update(node_ids)

    def reconnect(self, *node_ids: str) -> None:
        self.transport.unavailable.difference_update(node_ids)

    def client_knows(self, tail: int) -> None:
        self.client.observe_known_tail(tail)

    async def check_tail(self) -> CheckTailResult:
        return await self.client.check_tail()

    async def state_on(self, node_id: str) -> LogServerState:
        return await self.stores[node_id].state(self.segment_id)

    async def entry_on(self, node_id: str, position: int) -> LogEntry | None:
        return await self.stores[node_id].get(self.segment_id, position)

    @property
    def sequencer_known_tail(self) -> int:
        return self.sequencer.known_tail

    @property
    def client_known_tail(self) -> int:
        return self.client.known_tail
