from .errors import EntryConflict, PositionTrimmed, PredecessorUnavailable, SegmentSealed
from .types import LogEntry, LogServerState


class MemoryLogletStore:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self._entries: dict[tuple[str, int], LogEntry] = {}
        self._sealed: set[str] = set()
        self._trimmed_prefixes: dict[str, int] = {}
        self._known_tails: dict[str, int] = {}

    async def put(self, entry: LogEntry, known_tail: int = 0) -> None:
        self._observe_known_tail(entry.segment_id, known_tail)
        if entry.segment_id in self._sealed:
            raise SegmentSealed(entry.segment_id)
        key = (entry.segment_id, entry.position)
        existing = self._entries.get(key)
        if existing is not None:
            if existing != entry:
                raise EntryConflict(f"conflict at {key}")
            return
        if (
            entry.position > 0
            and (entry.segment_id, entry.position - 1) not in self._entries
            and self._known_tails[entry.segment_id] <= entry.position - 1
        ):
            raise PredecessorUnavailable(f"missing predecessor for {key}")
        await self._write(entry)

    async def repair(self, entry: LogEntry, known_tail: int = 0) -> None:
        self._observe_known_tail(entry.segment_id, known_tail)
        await self._write(entry)

    async def _write(self, entry: LogEntry) -> None:
        if entry.position < self._trimmed_prefixes.get(entry.segment_id, 0):
            raise PositionTrimmed(f"trimmed position {(entry.segment_id, entry.position)}")
        key = (entry.segment_id, entry.position)
        existing = self._entries.get(key)
        if existing is not None and existing != entry:
            raise EntryConflict(f"conflict at {key}")
        self._entries[key] = entry

    async def get(self, segment_id: str, position: int, known_tail: int = 0) -> LogEntry | None:
        self._observe_known_tail(segment_id, known_tail)
        return self._entries.get((segment_id, position))

    async def entries(
        self, segment_id: str, start: int = 0, limit: int = 100
    ) -> tuple[LogEntry, ...]:
        matches = sorted(
            (
                entry
                for (entry_segment, position), entry in self._entries.items()
                if entry_segment == segment_id and position >= start
            ),
            key=lambda entry: entry.position,
        )
        return tuple(matches[:limit])

    async def prefix_trim(self, segment_id: str, trim_position: int) -> int:
        if trim_position < 0:
            raise ValueError("prefixTrim requires a non-negative position")
        watermark = max(self._trimmed_prefixes.get(segment_id, 0), trim_position)
        self._trimmed_prefixes[segment_id] = watermark
        self._entries = {
            key: entry
            for key, entry in self._entries.items()
            if key[0] != segment_id or key[1] >= watermark
        }
        return watermark

    async def seal(self, segment_id: str, known_tail: int = 0) -> None:
        self._observe_known_tail(segment_id, known_tail)
        self._sealed.add(segment_id)

    async def state(self, segment_id: str, known_tail: int = 0) -> LogServerState:
        self._observe_known_tail(segment_id, known_tail)
        positions = [
            position for (entry_segment, position) in self._entries if entry_segment == segment_id
        ]
        return LogServerState(
            segment_id=segment_id,
            local_tail=max(
                max(positions, default=-1) + 1,
                self._trimmed_prefixes.get(segment_id, 0),
            ),
            trimmed_prefix=self._trimmed_prefixes.get(segment_id, 0),
            known_tail=self._known_tails[segment_id],
            sealed=segment_id in self._sealed,
        )

    async def segment_states(self) -> tuple[LogServerState, ...]:
        segment_ids = sorted(
            self._sealed
            | self._trimmed_prefixes.keys()
            | {segment_id for segment_id, _ in self._entries}
        )
        return tuple([await self.state(segment_id) for segment_id in segment_ids])

    def _observe_known_tail(self, segment_id: str, known_tail: int) -> None:
        self._known_tails[segment_id] = max(
            self._known_tails.get(segment_id, 0),
            known_tail,
        )
