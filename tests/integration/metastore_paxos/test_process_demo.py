from pathlib import Path

from delos_lab.metastore.paxos.demo import run_demo


async def test_three_process_demo_has_no_leader_and_recovers_peer(
    tmp_path: Path,
) -> None:
    events = await run_demo(tmp_path, timeout=15.0)

    assert [event.kind for event in events] == [
        "cluster_started",
        "cas_decided",
        "peer_stopped",
        "cas_decided_with_one_peer_down",
        "barrier_read",
        "peer_restarted",
        "peer_caught_up",
    ]
    assert events[1].details["version"] == 1
    assert events[3].details["version"] == 2
    assert events[4].details["version"] == 2
    assert events[6].details["version"] == 2
    assert all("leader_id" not in event.details for event in events)


async def test_restarted_peer_reuses_its_sqlite_file(tmp_path: Path) -> None:
    events = await run_demo(tmp_path, timeout=15.0)
    restarted = str(events[5].details["node_id"])

    assert (tmp_path / f"{restarted}.sqlite3").is_file()
