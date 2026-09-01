import json

from delos_lab.native_loglet.virtual_log_demo import main, run_demo


async def test_demo_exposes_chain_extension_as_one_virtual_log() -> None:
    events = await run_demo()

    assert [event.kind for event in events] == [
        "chain_bootstrapped",
        "append_committed",
        "segment_sealed",
        "chain_extended",
        "append_committed",
        "virtual_log_read",
    ]
    assert events[1].details["position"] == 0
    assert events[4].details["position"] == 1
    assert events[-1].details["entry_count"] == 2


def test_demo_main_prints_json_lines(capsys) -> None:
    main()

    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["kind"] for line in lines] == [
        "chain_bootstrapped",
        "append_committed",
        "segment_sealed",
        "chain_extended",
        "append_committed",
        "virtual_log_read",
    ]
