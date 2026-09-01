from pathlib import Path

import pytest

from delos_lab.kv.errors import ReconfigurationUnavailable
from delos_lab.kv.materializer import KvMaterializer
from delos_lab.kv.service import KvService
from delos_lab.kv.sqlite_store import SQLiteKvStore
from delos_lab.kv.types import Delete, Increment, KvCommandEnvelope, Put
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.virtual_log.loglet import LogletTail, LogletUnavailable
from delos_lab.virtual_log.types import (
    LogChain,
    LogSegment,
    VersionedLogChain,
    VirtualLogEntry,
)

MEMBERS = ("db-1", "db-2", "db-3")


def segment(segment_id: str = "s1", sequencer: str = "db-1") -> LogSegment:
    return LogSegment(
        segment_id=segment_id,
        virtual_start=0,
        virtual_stop=None,
        loglet=native_loglet_configuration(
            MEMBERS,
            sequencer,
            f"inc-{sequencer}",
        ),
    )


class FakeVirtualLog:
    def __init__(self, *, fail_once: bool = False, sealed_tail_once: bool = False) -> None:
        self._cached = VersionedLogChain(version=1, chain=LogChain(segments=(segment(),)))
        self.entries: list[tuple[str, bytes]] = []
        self.fail_once = fail_once
        self.sealed_tail_once = sealed_tail_once
        self.check_tail_reads = 0

    @property
    def cached(self) -> VersionedLogChain:
        return self._cached

    async def refresh(self) -> VersionedLogChain:
        return self._cached

    async def append(self, command_id: str, payload: bytes) -> int:
        if self.fail_once:
            self.fail_once = False
            raise LogletUnavailable("db-1")
        self.entries.append((command_id, payload))
        return len(self.entries) - 1

    async def read_next(self, virtual_start: int, virtual_stop: int) -> VirtualLogEntry | None:
        if virtual_start >= len(self.entries) or virtual_start >= virtual_stop:
            return None
        position = virtual_start
        command_id, payload = self.entries[position]
        return VirtualLogEntry(
            position=position,
            command_id=command_id,
            payload=payload,
            segment_id=self._cached.chain.active.segment_id if self._cached.chain else "s1",
            local_position=position,
        )

    async def check_tail(self) -> LogletTail:
        self.check_tail_reads += 1
        if self.sealed_tail_once:
            self.sealed_tail_once = False
            return LogletTail(tail=len(self.entries), sealed=True)
        return LogletTail(tail=len(self.entries), sealed=False)


class FakePolicy:
    async def initial_segment(self) -> LogSegment:
        return segment()


async def configured_service(
    tmp_path: Path,
    *,
    fail_once: bool = False,
    sealed_tail_once: bool = False,
) -> tuple[KvService, FakeVirtualLog, SQLiteKvStore]:
    virtual_log = FakeVirtualLog(
        fail_once=fail_once,
        sealed_tail_once=sealed_tail_once,
    )
    store = SQLiteKvStore(tmp_path / "node.sqlite")
    await store.open()
    service = KvService(
        "db-3",
        virtual_log,
        KvMaterializer(virtual_log, store),
        store,
        FakePolicy(),
    )
    return service, virtual_log, store


async def test_submit_appends_then_materializes_and_deduplicates(tmp_path: Path) -> None:
    service, virtual_log, store = await configured_service(tmp_path)
    command = KvCommandEnvelope(client_id="c", request_id="r1", operation=Put(key="n", value=7))

    first = await service.submit(command)
    duplicate = await service.submit(command)

    assert first == duplicate
    assert first.value == 7
    assert len(virtual_log.entries) == 1
    await store.close()


async def test_get_materializes_check_tail_without_appending(tmp_path: Path) -> None:
    service, virtual_log, store = await configured_service(tmp_path)
    await service.submit(
        KvCommandEnvelope(client_id="c", request_id="r1", operation=Put(key="n", value=9))
    )
    before = len(virtual_log.entries)

    observed = await service.get("n")

    assert observed == 9
    assert len(virtual_log.entries) == before
    assert virtual_log.check_tail_reads == 1
    await store.close()


async def test_get_applies_missed_delete_through_check_tail(tmp_path: Path) -> None:
    service, virtual_log, store = await configured_service(tmp_path)
    await service.submit(
        KvCommandEnvelope(client_id="c", request_id="r1", operation=Put(key="n", value=9))
    )
    deleted = KvCommandEnvelope(
        client_id="other",
        request_id="r2",
        operation=Delete(key="n"),
    )
    await virtual_log.append(deleted.command_id, deleted.to_payload())

    observed = await service.get("n")

    assert observed is None
    assert await store.applied_position() == 1
    await store.close()


async def test_service_does_not_implement_sealed_loglet_recovery(tmp_path: Path) -> None:
    service, virtual_log, store = await configured_service(
        tmp_path,
        sealed_tail_once=True,
    )

    with pytest.raises(ReconfigurationUnavailable, match="sealed active Loglet"):
        await service.get("missing")

    assert virtual_log.check_tail_reads == 1
    await store.close()


async def test_service_translates_exhausted_virtual_log_recovery(tmp_path: Path) -> None:
    service, virtual_log, store = await configured_service(tmp_path, fail_once=True)

    with pytest.raises(ReconfigurationUnavailable, match="could not append"):
        await service.submit(
            KvCommandEnvelope(
                client_id="c",
                request_id="r1",
                operation=Increment(key="n", delta=1),
            )
        )

    assert virtual_log.entries == []
    await store.close()
