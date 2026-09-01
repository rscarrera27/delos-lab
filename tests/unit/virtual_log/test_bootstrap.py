import asyncio

import pytest

from delos_lab.metastore.memory import MemoryMetaStore
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.virtual_log.core import ChainUnavailable, VirtualLog
from delos_lab.virtual_log.types import LogSegment


def segment(segment_id: str) -> LogSegment:
    return LogSegment(
        segment_id=segment_id,
        virtual_start=0,
        virtual_stop=None,
        loglet=native_loglet_configuration(
            ("db-1", "db-2", "db-3"),
            "db-1",
            f"inc-{segment_id}",
        ),
    )


def test_cached_chain_is_unavailable_before_bootstrap() -> None:
    virtual_log = VirtualLog(MemoryMetaStore())

    with pytest.raises(ChainUnavailable):
        _ = virtual_log.cached


async def test_concurrent_bootstrap_adopts_one_installed_chain() -> None:
    store = MemoryMetaStore()
    left = VirtualLog(store)
    right = VirtualLog(store)

    left_result, right_result = await asyncio.gather(
        left.bootstrap(segment("s-left")),
        right.bootstrap(segment("s-right")),
    )

    assert left_result == right_result
    assert left_result.version == 1
    assert left.cached == right.cached == left_result


async def test_refresh_adopts_chain_bootstrapped_by_another_client() -> None:
    store = MemoryMetaStore()
    writer = VirtualLog(store)
    reader = VirtualLog(store)
    installed = await writer.bootstrap(segment("s1"))

    refreshed = await reader.refresh()

    assert refreshed == installed
    assert reader.cached == installed
