from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.config import native_loglet_configuration
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.replacement import NativeLogletReplacementPreparer
from delos_lab.native_loglet.sequencer import NativeSequencer
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.virtual_log.types import LogSegment


async def test_prepares_and_validates_sealed_native_loglet_replacement() -> None:
    all_members = tuple(f"db-{index}" for index in range(1, 6))
    source_members = all_members[:3]
    target_members = all_members[2:]
    stores = {node: MemoryLogletStore(node) for node in all_members}
    transport = DirectLogletTransport(stores)
    sequencer = NativeSequencer("s", "db-1", source_members, transport)
    source_client = NativeLogletClient("s", source_members, transport)
    await sequencer.append("r1", b"first")
    await sequencer.append("r2", b"second")
    await source_client.seal()
    segment = LogSegment(
        segment_id="s",
        virtual_start=10,
        virtual_stop=12,
        loglet=native_loglet_configuration(source_members, "db-1", "inc-1"),
    )
    replacement = native_loglet_configuration(target_members, "db-3", "inc-3")

    update = await NativeLogletReplacementPreparer(transport).prepare(segment, replacement)

    assert (update.segment_id, update.loglet) == ("s", replacement)
    for node in target_members:
        state = await stores[node].state("s")
        assert (state.local_tail, state.sealed) == (2, True)
        assert await stores[node].get("s", 0) is not None
        assert await stores[node].get("s", 1) is not None


async def test_replacement_propagates_quorum_certified_trim_watermark() -> None:
    all_members = tuple(f"db-{index}" for index in range(1, 6))
    source_members = all_members[:3]
    target_members = all_members[2:]
    stores = {node: MemoryLogletStore(node) for node in all_members}
    transport = DirectLogletTransport(stores)
    sequencer = NativeSequencer("s", "db-1", source_members, transport)
    source_client = NativeLogletClient("s", source_members, transport)
    await sequencer.append("r1", b"first")
    await sequencer.append("r2", b"second")
    await source_client.seal()
    await source_client.prefix_trim(1)
    segment = LogSegment(
        segment_id="s",
        virtual_start=10,
        virtual_stop=12,
        loglet=native_loglet_configuration(source_members, "db-1", "inc-1"),
    )
    replacement = native_loglet_configuration(target_members, "db-3", "inc-3")

    await NativeLogletReplacementPreparer(transport).prepare(segment, replacement)

    target_client = NativeLogletClient("s", target_members, transport)
    result = await target_client.check_tail()
    assert (result.tail, result.sealed) == (2, True)
    trimmed = [(await stores[node].state("s")).trimmed_prefix >= 1 for node in target_members]
    assert sum(trimmed) >= 2
