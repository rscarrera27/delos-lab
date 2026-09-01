import pytest

from delos_lab.native_loglet.client import NativeLogletClient
from delos_lab.native_loglet.errors import NoQuorum
from delos_lab.native_loglet.types import CheckTailResult
from tests.support.native_loglet import NativeLogletScenario


async def test_prefix_trim_completes_on_a_quorum() -> None:
    scenario = NativeLogletScenario()
    await scenario.append("r1", b"first")
    await scenario.append("r2", b"second")
    scenario.disconnect("db-3")

    assert await scenario.client.prefix_trim(1) == 1
    assert (await scenario.state_on("db-1")).trimmed_prefix == 1
    assert (await scenario.state_on("db-2")).trimmed_prefix == 1
    assert (await scenario.state_on("db-3")).trimmed_prefix == 0


async def test_prefix_trim_rejects_less_than_a_quorum() -> None:
    scenario = NativeLogletScenario()
    scenario.disconnect("db-2", "db-3")

    with pytest.raises(NoQuorum):
        await scenario.client.prefix_trim(1)


async def test_new_client_check_tail_skips_physically_trimmed_prefix() -> None:
    scenario = NativeLogletScenario()
    await scenario.append("r1", b"first")
    await scenario.append("r2", b"second")
    await scenario.client.seal()
    await scenario.client.prefix_trim(2)
    fresh = NativeLogletClient(scenario.segment_id, scenario.members, scenario.transport)

    assert await fresh.check_tail() == CheckTailResult(tail=2, sealed=True)
