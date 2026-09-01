import asyncio
import json
from pathlib import Path

import aiosqlite
from pydantic import TypeAdapter

from .snapshot import KvSnapshot, KvSnapshotData, SnapshotAppliedEntry, SnapshotRequest
from .state_machine import KvStateMachine
from .types import KvCommandEnvelope, KvResult, KvValue

_VALUE_ADAPTER: TypeAdapter[KvValue] = TypeAdapter(KvValue)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv_items (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv_requests (
    client_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    command_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    first_position INTEGER NOT NULL CHECK (first_position >= 0),
    PRIMARY KEY (client_id, request_id)
);
CREATE TABLE IF NOT EXISTS kv_applied_entries (
    position INTEGER PRIMARY KEY CHECK (position >= 0),
    command_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv_progress (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    applied_position INTEGER NOT NULL CHECK (applied_position >= -1)
);
INSERT OR IGNORE INTO kv_progress(singleton, applied_position) VALUES (1, -1);
"""


class RequestConflict(Exception):
    pass


class SQLiteKvStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLiteKvStore is not open")
        return self._db

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.executescript(_SCHEMA)
        await self.db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def applied_position(self) -> int:
        async with self.db.execute(
            "SELECT applied_position FROM kv_progress WHERE singleton = 1"
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("KV progress row is missing")
        return int(row[0])

    async def get(self, key: str) -> KvValue | None:
        async with self.db.execute(
            "SELECT value_json FROM kv_items WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        return None if row is None else _VALUE_ADAPTER.validate_python(json.loads(str(row[0])))

    async def snapshot(self) -> dict[str, KvValue]:
        async with self.db.execute("SELECT key, value_json FROM kv_items ORDER BY key") as cursor:
            rows = await cursor.fetchall()
        return {
            str(key): _VALUE_ADAPTER.validate_python(json.loads(str(value_json)))
            for key, value_json in rows
        }

    async def request_count(self) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM kv_requests") as cursor:
            row = await cursor.fetchone()
        return 0 if row is None else int(row[0])

    async def export_snapshot(self) -> KvSnapshot:
        """Capture materialized state, deduplication, and progress atomically."""
        async with self._lock:
            await self.db.execute("BEGIN")
            try:
                values = await self.snapshot()
                applied_position = await self.applied_position()
                async with self.db.execute(
                    """
                    SELECT client_id, request_id, command_json, result_json, first_position
                    FROM kv_requests
                    ORDER BY client_id, request_id
                    """
                ) as cursor:
                    request_rows = await cursor.fetchall()
                async with self.db.execute(
                    """
                    SELECT position, command_json, result_json
                    FROM kv_applied_entries
                    ORDER BY position
                    """
                ) as cursor:
                    applied_rows = await cursor.fetchall()
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

        data = KvSnapshotData(
            applied_position=applied_position,
            values=values,
            requests=tuple(
                SnapshotRequest(
                    client_id=str(client_id),
                    request_id=str(request_id),
                    command=KvCommandEnvelope.model_validate_json(str(command_json)),
                    result=KvResult.model_validate_json(str(result_json)),
                    first_position=int(first_position),
                )
                for client_id, request_id, command_json, result_json, first_position in request_rows
            ),
            applied_entries=tuple(
                SnapshotAppliedEntry(
                    position=int(position),
                    command=KvCommandEnvelope.model_validate_json(str(command_json)),
                    result=KvResult.model_validate_json(str(result_json)),
                )
                for position, command_json, result_json in applied_rows
            ),
        )
        return KvSnapshot.create(data)

    async def install_snapshot(self, snapshot: KvSnapshot) -> None:
        """Atomically initialize a pristine replica from an application snapshot."""
        await self._restore_snapshot(snapshot, require_pristine=True)

    async def replace_bootstrap_snapshot(self, snapshot: KvSnapshot) -> None:
        """Rebase a not-yet-serving replica after its required log suffix was trimmed."""
        await self._restore_snapshot(snapshot, require_pristine=False)

    async def _restore_snapshot(
        self,
        snapshot: KvSnapshot,
        *,
        require_pristine: bool,
    ) -> None:
        snapshot = KvSnapshot.model_validate(snapshot.model_dump(mode="json"))
        async with self._lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                if require_pristine and not await self._is_pristine():
                    raise RuntimeError("KV snapshot installation requires a pristine store")
                for table in ("kv_items", "kv_requests", "kv_applied_entries"):
                    await self.db.execute(f"DELETE FROM {table}")
                await self.db.executemany(
                    "INSERT INTO kv_items(key, value_json) VALUES (?, ?)",
                    (
                        (key, json.dumps(value, separators=(",", ":")))
                        for key, value in snapshot.data.values.items()
                    ),
                )
                await self.db.executemany(
                    """
                    INSERT INTO kv_requests(
                        client_id, request_id, command_json, result_json, first_position
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            row.client_id,
                            row.request_id,
                            row.command.model_dump_json(),
                            row.result.model_dump_json(),
                            row.first_position,
                        )
                        for row in snapshot.data.requests
                    ),
                )
                await self.db.executemany(
                    """
                    INSERT INTO kv_applied_entries(position, command_json, result_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        (
                            row.position,
                            row.command.model_dump_json(),
                            row.result.model_dump_json(),
                        )
                        for row in snapshot.data.applied_entries
                    ),
                )
                await self.db.execute(
                    "UPDATE kv_progress SET applied_position = ? WHERE singleton = 1",
                    (snapshot.data.applied_position,),
                )
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def _is_pristine(self) -> bool:
        if await self.applied_position() != -1:
            return False
        for table in ("kv_items", "kv_requests", "kv_applied_entries"):
            async with self.db.execute(f"SELECT 1 FROM {table} LIMIT 1") as cursor:
                if await cursor.fetchone() is not None:
                    return False
        return True

    async def request(
        self, client_id: str, request_id: str
    ) -> tuple[KvCommandEnvelope, KvResult] | None:
        async with self.db.execute(
            "SELECT command_json, result_json FROM kv_requests WHERE client_id = ? AND request_id = ?",
            (client_id, request_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return (
            KvCommandEnvelope.model_validate_json(str(row[0])),
            KvResult.model_validate_json(str(row[1])),
        )

    async def result_at_position(self, position: int) -> KvResult | None:
        async with self.db.execute(
            "SELECT result_json FROM kv_applied_entries WHERE position = ?", (position,)
        ) as cursor:
            row = await cursor.fetchone()
        return None if row is None else KvResult.model_validate_json(str(row[0]))

    async def apply(self, position: int, command: KvCommandEnvelope) -> KvResult:
        command_json = command.model_dump_json()
        async with self._lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                current = await self.applied_position()
                if position <= current:
                    result = await self._existing_position(position, command_json)
                    await self.db.commit()
                    return result
                prior = await self.request(command.client_id, command.request_id)
                if prior is not None:
                    prior_command, result = prior
                    if prior_command != command:
                        raise RequestConflict(command.command_id)
                else:
                    machine = KvStateMachine(await self.snapshot())
                    result = machine.apply(command.operation)
                    await self._replace_snapshot(machine.snapshot)
                    await self.db.execute(
                        """
                        INSERT INTO kv_requests(
                            client_id, request_id, command_json, result_json, first_position
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            command.client_id,
                            command.request_id,
                            command_json,
                            result.model_dump_json(),
                            position,
                        ),
                    )

                await self.db.execute(
                    "INSERT INTO kv_applied_entries(position, command_json, result_json) VALUES (?, ?, ?)",
                    (position, command_json, result.model_dump_json()),
                )
                await self.db.execute(
                    "UPDATE kv_progress SET applied_position = ? WHERE singleton = 1", (position,)
                )
                await self.db.commit()
                return result
            except BaseException:
                await self.db.rollback()
                raise

    async def _existing_position(self, position: int, command_json: str) -> KvResult:
        async with self.db.execute(
            "SELECT command_json, result_json FROM kv_applied_entries WHERE position = ?",
            (position,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or str(row[0]) != command_json:
            raise RequestConflict(f"position {position} contains another command")
        return KvResult.model_validate_json(str(row[1]))

    async def _replace_snapshot(self, snapshot: dict[str, KvValue]) -> None:
        await self.db.execute("DELETE FROM kv_items")
        await self.db.executemany(
            "INSERT INTO kv_items(key, value_json) VALUES (?, ?)",
            ((key, json.dumps(value, separators=(",", ":"))) for key, value in snapshot.items()),
        )
