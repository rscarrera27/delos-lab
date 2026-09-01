import asyncio
import time

from delos_lab.common.events import EventValue, LabEvent

from .client import NativeLogletClient
from .memory_store import MemoryLogletStore
from .sequencer import NativeSequencer
from .transport import DirectLogletTransport


def _event(kind: str, details: dict[str, EventValue]) -> LabEvent:
    return LabEvent(
        timestamp=time.monotonic(),
        component="native-loglet",
        kind=kind,
        details=details,
    )


async def run_demo() -> list[LabEvent]:
    stores = {name: MemoryLogletStore(name) for name in ("db-1", "db-2", "db-3")}
    transport = DirectLogletTransport(stores)
    sequencer = NativeSequencer("segment-a", "db-1", tuple(stores), transport)
    client = NativeLogletClient("segment-a", tuple(stores), transport)
    events: list[LabEvent] = []

    first = await sequencer.append("request-1", b'{"op":"put","key":"x"}')
    client.observe_known_tail(first.known_tail)
    events.append(_event("append_committed", {"position": first.position}))

    transport.unavailable.add("db-3")
    events.append(_event("log_server_stopped", {"node_id": "db-3"}))

    second = await sequencer.append("request-2", b'{"op":"put","key":"y"}')
    client.observe_known_tail(second.known_tail)
    events.append(_event("append_committed", {"position": second.position}))

    await client.seal()
    events.append(_event("segment_sealed", {"segment_id": "segment-a"}))

    transport.unavailable.remove("db-3")
    result = await client.check_tail()
    events.append(_event("tail_repaired", {"tail": result.tail, "sealed": result.sealed}))
    return events


def main() -> None:
    for event in asyncio.run(run_demo()):
        print(event.model_dump_json())
