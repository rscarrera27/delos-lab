import asyncio

from delos_lab.metastore.memory import MemoryMetaStore
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


async def test_metastore_starts_at_version_zero_without_a_chain() -> None:
    store = MemoryMetaStore()

    assert await store.read() == VersionedLogChain(version=0, chain=None)


async def test_compare_and_set_applies_one_version_and_reports_winner() -> None:
    store = MemoryMetaStore()
    first = chain("s1")
    other = chain("s2")

    applied = await store.compare_and_set(0, first)
    mismatch = await store.compare_and_set(0, other)

    assert isinstance(applied, Applied)
    assert applied.snapshot == VersionedLogChain(version=1, chain=first)
    assert isinstance(mismatch, VersionMismatch)
    assert mismatch.current == applied.snapshot
    assert await store.read() == applied.snapshot


async def test_concurrent_compare_and_set_has_exactly_one_winner() -> None:
    store = MemoryMetaStore()

    results = await asyncio.gather(
        store.compare_and_set(0, chain("s-left")),
        store.compare_and_set(0, chain("s-right")),
    )

    assert sum(isinstance(result, Applied) for result in results) == 1
    assert sum(isinstance(result, VersionMismatch) for result in results) == 1
    assert (await store.read()).version == 1
