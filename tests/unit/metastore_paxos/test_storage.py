from delos_lab.metastore.paxos.storage import MemoryPaxosStorage
from delos_lab.metastore.paxos.types import PersistentPaxosState


async def test_memory_storage_returns_the_last_saved_state() -> None:
    storage = MemoryPaxosStorage()
    state = PersistentPaxosState(local_round=7)

    await storage.save(state)

    assert await storage.load() == state


async def test_memory_storage_can_start_from_a_saved_state() -> None:
    state = PersistentPaxosState(local_round=4)
    storage = MemoryPaxosStorage(state)

    assert await storage.load() == state
