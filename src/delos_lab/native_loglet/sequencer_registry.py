import asyncio
from dataclasses import dataclass

from delos_lab.virtual_log.types import LogSegment

from .config import NativeLogletConfiguration
from .errors import EntryConflict, IncarnationMismatch, NotSequencer
from .sequencer import NativeSequencer
from .transport import LogletTransport
from .types import AppendResult


@dataclass(frozen=True)
class SequencerObservation:
    segment_id: str
    known_tail: int


@dataclass(frozen=True)
class _PendingAppend:
    command_id: str
    payload: bytes
    task: asyncio.Task[AppendResult]


class LogServerSequencerRegistry:
    """Sequencer instances owned by one colocated LogServer component."""

    def __init__(
        self,
        node_id: str,
        incarnation_id: str,
        transport: LogletTransport,
    ) -> None:
        self.node_id = node_id
        self.incarnation_id = incarnation_id
        self._transport = transport
        self._sequencers: dict[str, NativeSequencer] = {}
        self._segments: dict[str, LogSegment] = {}
        self._pending: dict[str, _PendingAppend] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def append(
        self,
        segment: LogSegment,
        command_id: str,
        payload: bytes,
    ) -> AppendResult:
        sequencer = await self._sequencer_for(segment)
        while True:
            async with self._lock:
                if self._closed:
                    raise RuntimeError("sequencer registry is closed")
                pending = self._pending.get(segment.segment_id)
                same_command = pending is not None and pending.command_id == command_id
                if same_command and pending is not None and pending.payload != payload:
                    raise EntryConflict(command_id)
                if pending is None:
                    task = asyncio.create_task(
                        self._run_append(segment.segment_id, sequencer, command_id, payload)
                    )
                    pending = _PendingAppend(command_id, payload, task)
                    self._pending[segment.segment_id] = pending
                    same_command = True

            if same_command:
                return await asyncio.shield(pending.task)

            await asyncio.shield(pending.task)

    async def _sequencer_for(self, segment: LogSegment) -> NativeSequencer:
        configuration = NativeLogletConfiguration.from_generic(segment.loglet)
        if configuration.sequencer_node != self.node_id:
            raise NotSequencer(configuration.sequencer_node)
        if configuration.sequencer_incarnation != self.incarnation_id:
            raise IncarnationMismatch(configuration.sequencer_incarnation)
        async with self._lock:
            existing = self._segments.get(segment.segment_id)
            if existing is not None and existing.loglet != segment.loglet:
                raise IncarnationMismatch(f"segment {segment.segment_id} configuration changed")
            sequencer = self._sequencers.get(segment.segment_id)
            if sequencer is None:
                sequencer = NativeSequencer(
                    segment.segment_id,
                    self.node_id,
                    configuration.storage_members,
                    self._transport,
                )
                self._segments[segment.segment_id] = segment
                self._sequencers[segment.segment_id] = sequencer
        return sequencer

    async def observe(self, segment: LogSegment) -> SequencerObservation | None:
        """Return this incarnation's sequencer knowledge without creating runtime state."""
        configuration = NativeLogletConfiguration.from_generic(segment.loglet)
        if (
            configuration.sequencer_node != self.node_id
            or configuration.sequencer_incarnation != self.incarnation_id
        ):
            return None
        async with self._lock:
            sequencer = self._sequencers.get(segment.segment_id)
            known_tail = 0 if sequencer is None else sequencer.known_tail
        return SequencerObservation(segment_id=segment.segment_id, known_tail=known_tail)

    async def _run_append(
        self,
        segment_id: str,
        sequencer: NativeSequencer,
        command_id: str,
        payload: bytes,
    ) -> AppendResult:
        try:
            return await sequencer.append(command_id, payload)
        finally:
            current = asyncio.current_task()
            async with self._lock:
                pending = self._pending.get(segment_id)
                if pending is not None and pending.task is current:
                    self._pending.pop(segment_id, None)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            tasks = tuple(pending.task for pending in self._pending.values())
            for task in tasks:
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
