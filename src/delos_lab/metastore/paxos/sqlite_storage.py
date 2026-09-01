import asyncio
from pathlib import Path

import aiosqlite

from delos_lab.virtual_log.types import LogChain, VersionedLogChain

from .types import AcceptorSlotState, Ballot, PaxosValue, PersistentPaxosState


class SQLitePaxosStorage:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("SQLitePaxosStorage is not open")
        return self._connection

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self._path)
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS paxos_node (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                local_round INTEGER NOT NULL,
                last_applied INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paxos_slots (
                slot INTEGER PRIMARY KEY,
                promised_ballot_json TEXT,
                accepted_ballot_json TEXT,
                accepted_value_json TEXT,
                decided_value_json TEXT
            );
            CREATE TABLE IF NOT EXISTS version_register (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                version INTEGER NOT NULL,
                chain_json TEXT
            );
            INSERT OR IGNORE INTO paxos_node
                (singleton, local_round, last_applied) VALUES (1, 0, 0);
            INSERT OR IGNORE INTO version_register
                (singleton, version, chain_json) VALUES (1, 0, NULL);
            """
        )
        await connection.commit()
        self._connection = connection

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def journal_mode(self) -> str:
        connection = self._require_connection()
        cursor = await connection.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("SQLite did not return a journal mode")
        return str(row[0]).lower()

    async def load(self) -> PersistentPaxosState:
        async with self._lock:
            connection = self._require_connection()
            node_cursor = await connection.execute(
                "SELECT local_round, last_applied FROM paxos_node WHERE singleton = 1"
            )
            node_row = await node_cursor.fetchone()
            register_cursor = await connection.execute(
                "SELECT version, chain_json FROM version_register WHERE singleton = 1"
            )
            register_row = await register_cursor.fetchone()
            slot_cursor = await connection.execute(
                "SELECT slot, promised_ballot_json, accepted_ballot_json, "
                "accepted_value_json, decided_value_json "
                "FROM paxos_slots ORDER BY slot"
            )
            slot_rows = await slot_cursor.fetchall()
            if node_row is None or register_row is None:
                raise RuntimeError("Paxos SQLite state is missing")

            slots = tuple(
                AcceptorSlotState(
                    slot=int(slot),
                    promised_ballot=(
                        None if promised_json is None else Ballot.model_validate_json(promised_json)
                    ),
                    accepted_ballot=(
                        None
                        if accepted_ballot_json is None
                        else Ballot.model_validate_json(accepted_ballot_json)
                    ),
                    accepted_value=(
                        None
                        if accepted_value_json is None
                        else PaxosValue.model_validate_json(accepted_value_json)
                    ),
                    decided_value=(
                        None
                        if decided_value_json is None
                        else PaxosValue.model_validate_json(decided_value_json)
                    ),
                )
                for (
                    slot,
                    promised_json,
                    accepted_ballot_json,
                    accepted_value_json,
                    decided_value_json,
                ) in slot_rows
            )
            chain = (
                None if register_row[1] is None else LogChain.model_validate_json(register_row[1])
            )
            return PersistentPaxosState(
                local_round=int(node_row[0]),
                slots=slots,
                last_applied=int(node_row[1]),
                state_machine=VersionedLogChain(
                    version=int(register_row[0]),
                    chain=chain,
                ),
            )

    async def save(self, state: PersistentPaxosState) -> None:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute(
                    "UPDATE paxos_node SET local_round = ?, last_applied = ? WHERE singleton = 1",
                    (state.local_round, state.last_applied),
                )
                await connection.execute("DELETE FROM paxos_slots")
                await connection.executemany(
                    "INSERT INTO paxos_slots "
                    "(slot, promised_ballot_json, accepted_ballot_json, "
                    "accepted_value_json, decided_value_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        (
                            slot.slot,
                            None
                            if slot.promised_ballot is None
                            else slot.promised_ballot.model_dump_json(),
                            None
                            if slot.accepted_ballot is None
                            else slot.accepted_ballot.model_dump_json(),
                            None
                            if slot.accepted_value is None
                            else slot.accepted_value.model_dump_json(),
                            None
                            if slot.decided_value is None
                            else slot.decided_value.model_dump_json(),
                        )
                        for slot in state.slots
                    ),
                )
                await connection.execute(
                    "UPDATE version_register SET version = ?, chain_json = ? WHERE singleton = 1",
                    (
                        state.state_machine.version,
                        None
                        if state.state_machine.chain is None
                        else state.state_machine.chain.model_dump_json(),
                    ),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
