import asyncio
from pathlib import Path

import aiosqlite

from .errors import EntryConflict, PositionTrimmed, PredecessorUnavailable, SegmentSealed
from .types import LogEntry, LogServerState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS loglet_segments (
    segment_id TEXT PRIMARY KEY,
    sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1)),
    trimmed_prefix INTEGER NOT NULL DEFAULT 0 CHECK (trimmed_prefix >= 0)
);
CREATE TABLE IF NOT EXISTS loglet_entries (
    segment_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    command_id TEXT NOT NULL,
    payload BLOB NOT NULL,
    PRIMARY KEY (segment_id, position),
    UNIQUE (segment_id, command_id)
);
"""


class SQLiteLogletStore:
    def __init__(self, node_id: str, path: Path) -> None:
        self.node_id = node_id
        self.path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._known_tails: dict[str, int] = {}

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLiteLogletStore is not open")
        return self._db

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.executescript(_SCHEMA)
        async with self.db.execute("PRAGMA table_info(loglet_segments)") as cursor:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        if "trimmed_prefix" not in columns:
            await self.db.execute(
                "ALTER TABLE loglet_segments ADD COLUMN trimmed_prefix INTEGER NOT NULL DEFAULT 0"
            )
        await self.db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def put(self, entry: LogEntry, known_tail: int = 0) -> None:
        self._observe_known_tail(entry.segment_id, known_tail)
        await self._write(entry, reject_if_sealed=True, require_predecessor=True)

    async def repair(self, entry: LogEntry, known_tail: int = 0) -> None:
        self._observe_known_tail(entry.segment_id, known_tail)
        await self._write(entry, reject_if_sealed=False, require_predecessor=False)

    async def _write(
        self,
        entry: LogEntry,
        *,
        reject_if_sealed: bool,
        require_predecessor: bool,
    ) -> None:
        async with self._lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                await self.db.execute(
                    "INSERT OR IGNORE INTO loglet_segments(segment_id) VALUES (?)",
                    (entry.segment_id,),
                )
                if reject_if_sealed and await self._is_sealed(entry.segment_id):
                    raise SegmentSealed(entry.segment_id)
                if entry.position < await self._trimmed_prefix(entry.segment_id):
                    raise PositionTrimmed(f"trimmed position {(entry.segment_id, entry.position)}")

                existing = await self._entry_at(entry.segment_id, entry.position)
                if existing is not None:
                    if existing != entry:
                        raise EntryConflict(f"conflict at {(entry.segment_id, entry.position)}")
                    await self.db.commit()
                    return

                if (
                    require_predecessor
                    and entry.position > 0
                    and await self._entry_at(entry.segment_id, entry.position - 1) is None
                    and self._known_tails[entry.segment_id] <= entry.position - 1
                ):
                    raise PredecessorUnavailable(
                        f"missing predecessor for {(entry.segment_id, entry.position)}"
                    )

                command_position = await self._position_for_command(
                    entry.segment_id, entry.command_id
                )
                if command_position is not None and command_position != entry.position:
                    raise EntryConflict(
                        f"command {entry.command_id} already exists at {command_position}"
                    )

                await self.db.execute(
                    """
                    INSERT INTO loglet_entries(segment_id, position, command_id, payload)
                    VALUES (?, ?, ?, ?)
                    """,
                    (entry.segment_id, entry.position, entry.command_id, entry.payload),
                )
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def get(self, segment_id: str, position: int, known_tail: int = 0) -> LogEntry | None:
        self._observe_known_tail(segment_id, known_tail)
        return await self._entry_at(segment_id, position)

    async def entries(
        self, segment_id: str, start: int = 0, limit: int = 100
    ) -> tuple[LogEntry, ...]:
        async with self.db.execute(
            """
            SELECT position, command_id, payload
            FROM loglet_entries
            WHERE segment_id = ? AND position >= ?
            ORDER BY position
            LIMIT ?
            """,
            (segment_id, start, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return tuple(
            LogEntry(
                segment_id=segment_id,
                position=int(position),
                command_id=str(command_id),
                payload=bytes(payload),
            )
            for position, command_id, payload in rows
        )

    async def prefix_trim(self, segment_id: str, trim_position: int) -> int:
        if trim_position < 0:
            raise ValueError("prefixTrim requires a non-negative position")
        async with self._lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                await self.db.execute(
                    """
                    INSERT INTO loglet_segments(segment_id, trimmed_prefix)
                    VALUES (?, ?)
                    ON CONFLICT(segment_id) DO UPDATE SET
                        trimmed_prefix = MAX(trimmed_prefix, excluded.trimmed_prefix)
                    """,
                    (segment_id, trim_position),
                )
                watermark = await self._trimmed_prefix(segment_id)
                await self.db.execute(
                    "DELETE FROM loglet_entries WHERE segment_id = ? AND position < ?",
                    (segment_id, watermark),
                )
                await self.db.commit()
                return watermark
            except BaseException:
                await self.db.rollback()
                raise

    async def seal(self, segment_id: str, known_tail: int = 0) -> None:
        self._observe_known_tail(segment_id, known_tail)
        async with self._lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                await self.db.execute(
                    """
                    INSERT INTO loglet_segments(segment_id, sealed)
                    VALUES (?, 1)
                    ON CONFLICT(segment_id) DO UPDATE SET sealed = 1
                    """,
                    (segment_id,),
                )
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def state(self, segment_id: str, known_tail: int = 0) -> LogServerState:
        self._observe_known_tail(segment_id, known_tail)
        async with self.db.execute(
            "SELECT sealed, trimmed_prefix FROM loglet_segments WHERE segment_id = ?",
            (segment_id,),
        ) as cursor:
            segment = await cursor.fetchone()
        async with self.db.execute(
            "SELECT MAX(position) FROM loglet_entries WHERE segment_id = ?", (segment_id,)
        ) as cursor:
            tail_row = await cursor.fetchone()

        tail_value = None if tail_row is None else tail_row[0]
        return LogServerState(
            segment_id=segment_id,
            local_tail=max(
                0 if tail_value is None else int(tail_value) + 1,
                0 if segment is None else int(segment[1]),
            ),
            trimmed_prefix=0 if segment is None else int(segment[1]),
            known_tail=self._known_tails[segment_id],
            sealed=segment is not None and bool(segment[0]),
        )

    async def segment_states(self) -> tuple[LogServerState, ...]:
        async with self.db.execute(
            "SELECT segment_id FROM loglet_segments ORDER BY segment_id"
        ) as cursor:
            rows = await cursor.fetchall()
        return tuple([await self.state(str(row[0])) for row in rows])

    def _observe_known_tail(self, segment_id: str, known_tail: int) -> None:
        self._known_tails[segment_id] = max(
            self._known_tails.get(segment_id, 0),
            known_tail,
        )

    async def _is_sealed(self, segment_id: str) -> bool:
        async with self.db.execute(
            "SELECT sealed FROM loglet_segments WHERE segment_id = ?", (segment_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return row is not None and bool(row[0])

    async def _trimmed_prefix(self, segment_id: str) -> int:
        async with self.db.execute(
            "SELECT trimmed_prefix FROM loglet_segments WHERE segment_id = ?", (segment_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return 0 if row is None else int(row[0])

    async def _entry_at(self, segment_id: str, position: int) -> LogEntry | None:
        async with self.db.execute(
            """
            SELECT command_id, payload
            FROM loglet_entries
            WHERE segment_id = ? AND position = ?
            """,
            (segment_id, position),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return LogEntry(
            segment_id=segment_id,
            position=position,
            command_id=str(row[0]),
            payload=bytes(row[1]),
        )

    async def _position_for_command(self, segment_id: str, command_id: str) -> int | None:
        async with self.db.execute(
            """
            SELECT position
            FROM loglet_entries
            WHERE segment_id = ? AND command_id = ?
            """,
            (segment_id, command_id),
        ) as cursor:
            row = await cursor.fetchone()
        return None if row is None else int(row[0])
