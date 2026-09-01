import pytest

from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.errors import SegmentSealed
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.transport import DirectLogletTransport


async def test_quorum_seal_prevents_future_ack() -> None:
    stores = {name: MemoryLogletStore(name) for name in ("db-1", "db-2", "db-3")}
    transport = DirectLogletTransport(stores)
    sequencer = NativeSequencer("s", "db-1", tuple(stores), transport)
    client = NativeLogletClient("s", tuple(stores), transport)
    await sequencer.append("r1", b"a")

    await client.seal()

    with pytest.raises(SegmentSealed):
        await sequencer.append("r2", b"b")
