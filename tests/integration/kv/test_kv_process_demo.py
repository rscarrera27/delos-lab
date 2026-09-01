from pathlib import Path

from delos_lab.kv.demo import run_demo


async def test_demo_reconfigures_and_catches_up_restarted_node(tmp_path: Path) -> None:
    events = await run_demo(tmp_path, timeout=15.0)

    assert [event.kind for event in events] == [
        "cluster_started",
        "chain_bootstrapped",
        "put_applied",
        "database_replicas_agreed",
        "sequencer_stopped",
        "chain_reconfigured",
        "increment_applied",
        "peer_restarted",
        "peer_caught_up",
    ]
    assert events[-1].details["value"] == 2
