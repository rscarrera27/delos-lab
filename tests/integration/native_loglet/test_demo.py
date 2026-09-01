import json

from delos_lab.native_loglet.demo import main, run_demo


async def test_demo_exposes_quorum_seal_and_repair_sequence() -> None:
    events = await run_demo()

    assert [event.kind for event in events] == [
        "append_committed",
        "log_server_stopped",
        "append_committed",
        "segment_sealed",
        "tail_repaired",
    ]
    assert events[-1].details["tail"] == 2


def test_demo_main_prints_json_lines(capsys) -> None:
    main()

    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["kind"] for line in lines] == [
        "append_committed",
        "log_server_stopped",
        "append_committed",
        "segment_sealed",
        "tail_repaired",
    ]
