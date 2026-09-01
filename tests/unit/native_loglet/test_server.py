import asyncio

from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.server import NativeLogServer
from delos_lab.native_loglet.types import LogEntry


async def test_tail_notification_wakes_when_local_tail_reaches_target() -> None:
    server = NativeLogServer(MemoryLogletStore("db-1"))
    waiting = asyncio.create_task(server.wait_for_tail("s", 1))
    await asyncio.sleep(0)

    assert not waiting.done()

    await server.put(LogEntry(segment_id="s", position=0, command_id="r1", payload=b"a"))
    state = await asyncio.wait_for(waiting, timeout=1)
    assert (state.local_tail, state.sealed) == (1, False)


async def test_tail_notification_wakes_when_segment_seals_before_target() -> None:
    server = NativeLogServer(MemoryLogletStore("db-1"))
    waiting = asyncio.create_task(server.wait_for_tail("s", 10))
    await asyncio.sleep(0)

    await server.seal("s")

    state = await asyncio.wait_for(waiting, timeout=1)
    assert (state.local_tail, state.sealed) == (0, True)
