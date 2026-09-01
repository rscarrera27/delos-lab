import asyncio

import pytest

from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.errors import EntryConflict, NoQuorum
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.transport import DirectLogletTransport
from delos_lab.native_loglet.types import CheckTailResult
from tests.support.native_loglet import NativeLogletScenario


async def test_none_sealed_empty_loglet_has_open_tail_zero() -> None:
    scenario = NativeLogletScenario()

    assert await scenario.check_tail() == CheckTailResult(tail=0, sealed=False)


async def test_some_sealed_check_tail_seals_all_and_repairs_a_zombie_append() -> None:
    scenario = NativeLogletScenario()
    await scenario.append("r1", b"a")
    zombie = await scenario.write_local_copy("db-1", 1, command_id="r2", payload=b"b")
    await scenario.seal("db-1", "db-2", known_tail=1)

    result = await scenario.check_tail()

    assert result == CheckTailResult(tail=2, sealed=True)
    for node_id in scenario.members:
        assert (await scenario.state_on(node_id)).sealed is True
        assert await scenario.entry_on(node_id, 1) == zombie


async def test_check_tail_requires_a_responding_logserver_quorum() -> None:
    scenario = NativeLogletScenario()
    scenario.disconnect("db-2", "db-3")

    with pytest.raises(NoQuorum):
        await scenario.check_tail()


async def test_check_tail_waits_for_notification_instead_of_returning_partial_tail() -> None:
    scenario = NativeLogletScenario()
    await scenario.write_local_copy("db-1", 0, command_id="r1", payload=b"a")
    pending = asyncio.create_task(scenario.check_tail())
    await asyncio.sleep(0)

    assert not pending.done()

    await scenario.seal("db-1", "db-2")
    assert await asyncio.wait_for(pending, timeout=1) == CheckTailResult(tail=1, sealed=True)


async def test_check_tail_notification_observes_trailing_server_catch_up() -> None:
    scenario = NativeLogletScenario()
    entry = await scenario.write_local_copy("db-1", 0, command_id="r1", payload=b"a")
    pending = asyncio.create_task(scenario.check_tail())
    await asyncio.sleep(0)

    assert not pending.done()

    await scenario.transport.repair("db-2", entry)
    assert await asyncio.wait_for(pending, timeout=1) == CheckTailResult(tail=1, sealed=False)


async def test_check_tail_accepts_a_tail_present_on_a_quorum() -> None:
    scenario = NativeLogletScenario()
    await scenario.write_local_copy("db-1", 0, command_id="r1", payload=b"a")
    await scenario.write_local_copy("db-2", 0, command_id="r1", payload=b"a")

    result = await scenario.check_tail()

    assert result == CheckTailResult(tail=1, sealed=False)
    assert scenario.client_known_tail == 1


async def test_check_tail_accepts_client_known_tail_proof() -> None:
    scenario = NativeLogletScenario()
    scenario.client_knows(1)
    await scenario.write_local_copy("db-1", 0, command_id="r1", payload=b"a")

    assert await scenario.check_tail() == CheckTailResult(tail=1, sealed=False)


async def test_check_tail_rejects_conflicting_entries_during_sealed_repair() -> None:
    scenario = NativeLogletScenario()
    await scenario.write_local_copy("db-1", 0, command_id="r1", payload=b"a")
    await scenario.write_local_copy("db-2", 0, command_id="r2", payload=b"b")
    await scenario.seal(*scenario.members)

    with pytest.raises(EntryConflict):
        await scenario.check_tail()


def test_loglet_client_accepts_even_membership_with_a_strict_majority() -> None:
    stores = {name: MemoryLogletStore(name) for name in ("db-1", "db-2", "db-3", "db-4")}

    client = NativeLogletClient("s", tuple(stores), DirectLogletTransport(stores))

    assert client.quorum == 3
