import asyncio

from delos_lab.common.membership import quorum_size, validate_fixed_members

from .errors import EntryConflict, SegmentSealed
from .transport import LogletTransport
from .types import AppendResult, LogEntry, LogServerState


class NativeSequencer:
    def __init__(
        self,
        segment_id: str,
        sequencer_id: str,
        members: tuple[str, ...],
        transport: LogletTransport,
        *,
        retry_interval: float = 0.01,
        max_retry_interval: float = 0.25,
    ) -> None:
        members = validate_fixed_members(members, label="native Loglet storage")
        if sequencer_id not in members:
            raise ValueError("sequencer must be a storage member")
        self.segment_id = segment_id
        self.sequencer_id = sequencer_id
        self.members = members
        self.quorum = quorum_size(len(members))
        if retry_interval < 0 or max_retry_interval < retry_interval:
            raise ValueError("invalid append retry interval")
        self._transport = transport
        self._retry_interval = retry_interval
        self._max_retry_interval = max_retry_interval
        self._lock = asyncio.Lock()
        self._next_position = 0
        self._known_tail = 0
        self._entries_by_command: dict[str, LogEntry] = {}
        self._pending: LogEntry | None = None

    @property
    def known_tail(self) -> int:
        return self._known_tail

    def observe_known_tail(self, tail: int) -> None:
        self._known_tail = max(self._known_tail, tail)

    async def append(self, command_id: str, payload: bytes) -> AppendResult:
        async with self._lock:
            entry = self._entries_by_command.get(command_id)
            if entry is not None and entry.payload != payload:
                raise EntryConflict(command_id)
            if entry is not None and entry.position < self._known_tail:
                return AppendResult(position=entry.position, known_tail=self._known_tail)

            if self._pending is not None and self._pending.command_id != command_id:
                await self._commit(self._pending)
                self._pending = None

            if entry is None:
                entry = LogEntry(
                    segment_id=self.segment_id,
                    position=self._next_position,
                    command_id=command_id,
                    payload=payload,
                )
                self._entries_by_command[command_id] = entry
                self._next_position += 1
            self._pending = entry
            await self._commit(entry)
            self._pending = None
            return AppendResult(position=entry.position, known_tail=self._known_tail)

    async def _commit(self, entry: LogEntry) -> None:
        delay = self._retry_interval
        while True:
            replies = await asyncio.gather(
                *(self._transport.put(node, entry, self._known_tail) for node in self.members),
                return_exceptions=True,
            )
            states = [reply for reply in replies if isinstance(reply, LogServerState)]
            for state in states:
                self.observe_known_tail(state.known_tail)
            if len(states) >= self.quorum:
                break
            sealed = sum(isinstance(reply, SegmentSealed) for reply in replies)
            if sealed >= self.quorum:
                raise SegmentSealed(self.segment_id)
            await asyncio.sleep(delay)
            delay = min(self._max_retry_interval, max(self._retry_interval, delay * 2))
        if entry.position != self._known_tail:
            raise RuntimeError("sequencer committed prefix is not contiguous")
        self._known_tail = entry.position + 1
