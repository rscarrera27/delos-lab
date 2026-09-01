import asyncio
import time

from delos_lab.common.events import EventValue, LabEvent
from delos_lab.metastore.memory import MemoryMetaStore
from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.native_loglet.virtual_log_adapter import NativeLogletRuntime
from delos_lab.virtual_log.core import VirtualLog
from delos_lab.virtual_log.loglet import StaticLogletProvider
from delos_lab.virtual_log.types import LogSegment, NewLogletConfiguration

MEMBERS = ("db-1", "db-2", "db-3")


def _event(kind: str, details: dict[str, EventValue]) -> LabEvent:
    return LabEvent(
        timestamp=time.monotonic(),
        component="virtual-log",
        kind=kind,
        details=details,
    )


def _runtime(
    segment_id: str,
    sequencer_node: str,
    transport: DirectLogletTransport,
) -> NativeLogletRuntime:
    sequencer = NativeSequencer(segment_id, sequencer_node, MEMBERS, transport)
    client = NativeLogletClient(segment_id, MEMBERS, transport)
    return NativeLogletRuntime(sequencer, client, transport, MEMBERS)


async def run_demo() -> list[LabEvent]:
    stores = {member: MemoryLogletStore(member) for member in MEMBERS}
    transport = DirectLogletTransport(stores)
    first_runtime = _runtime("segment-a", "db-1", transport)
    second_runtime = _runtime("segment-b", "db-2", transport)
    virtual_log = VirtualLog(
        MemoryMetaStore(),
        StaticLogletProvider(
            {
                "segment-a": first_runtime,
                "segment-b": second_runtime,
            }
        ),
    )
    events: list[LabEvent] = []

    initial = LogSegment(
        segment_id="segment-a",
        virtual_start=0,
        virtual_stop=None,
        loglet=native_loglet_configuration(
            MEMBERS,
            "db-1",
            "inc-segment-a",
        ),
    )
    bootstrapped = await virtual_log.bootstrap(initial)
    events.append(
        _event(
            "chain_bootstrapped",
            {"version": bootstrapped.version, "segment_id": "segment-a"},
        )
    )

    first_position = await virtual_log.append("request-1", b"first")
    events.append(_event("append_committed", {"position": first_position}))

    applied = await virtual_log.reconfig_extend(
        NewLogletConfiguration(
            segment_id="segment-b",
            loglet=native_loglet_configuration(
                MEMBERS,
                "db-2",
                "inc-segment-b",
            ),
        )
    )
    assert applied is True
    extended = virtual_log.cached
    assert extended.chain is not None
    events.append(
        _event(
            "segment_sealed",
            {
                "segment_id": "segment-a",
                "virtual_stop": extended.chain.segments[0].virtual_stop or 0,
            },
        )
    )
    events.append(
        _event(
            "chain_extended",
            {
                "version": extended.version,
                "segment_id": extended.chain.active.segment_id,
                "virtual_start": extended.chain.active.virtual_start,
            },
        )
    )

    second_position = await virtual_log.append("request-2", b"second")
    events.append(_event("append_committed", {"position": second_position}))

    first_entry = await virtual_log.read(first_position)
    second_entry = await virtual_log.read(second_position)
    events.append(
        _event(
            "virtual_log_read",
            {
                "entry_count": 2,
                "first_segment": first_entry.segment_id,
                "second_segment": second_entry.segment_id,
            },
        )
    )
    return events


def main() -> None:
    for event in asyncio.run(run_demo()):
        print(event.model_dump_json())
