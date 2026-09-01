from delos_lab.native_loglet.types import CheckTailResult, LogEntry, LogServerState


def test_log_entry_round_trips_through_json() -> None:
    entry = LogEntry(
        segment_id="segment-a",
        position=3,
        command_id="client-1/request-7",
        payload=b'{"op":"put","key":"x"}',
    )

    assert LogEntry.model_validate_json(entry.model_dump_json()) == entry


def test_tail_types_use_first_uncommitted_positions() -> None:
    state = LogServerState(
        segment_id="segment-a",
        local_tail=0,
        known_tail=0,
        sealed=False,
    )

    assert state.local_tail == 0
    assert state.known_tail == 0
    assert CheckTailResult(tail=0, sealed=False).tail == 0
