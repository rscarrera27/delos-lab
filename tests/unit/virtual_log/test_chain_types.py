import pytest
from pydantic import ValidationError

from delos_lab.native_loglet.config import (
    NativeLogletConfiguration,
    native_loglet_configuration,
)
from delos_lab.virtual_log.types import LogChain, LogSegment, VersionedLogChain


def segment(segment_id: str, start: int, end: int | None) -> LogSegment:
    return LogSegment(
        segment_id=segment_id,
        virtual_start=start,
        virtual_stop=end,
        loglet=native_loglet_configuration(
            ("db-1", "db-2", "db-3"),
            "db-1",
            f"inc-{segment_id}",
        ),
    )


def test_chain_accepts_contiguous_segments_and_exposes_active_tail() -> None:
    chain = LogChain(segments=(segment("s1", 0, 2), segment("s2", 2, None)))

    assert chain.active.segment_id == "s2"


def test_chain_rejects_gap_between_segments() -> None:
    with pytest.raises(ValidationError, match="contiguous"):
        LogChain(segments=(segment("s1", 0, 2), segment("s2", 3, None)))


def test_chain_rejects_open_segment_before_tail() -> None:
    with pytest.raises(ValidationError, match="last segment"):
        LogChain(segments=(segment("s1", 0, None), segment("s2", 0, None)))


def test_chain_rejects_duplicate_segment_id() -> None:
    with pytest.raises(ValidationError, match="unique"):
        LogChain(segments=(segment("s1", 0, 1), segment("s1", 1, None)))


def test_chain_allows_an_empty_closed_segment() -> None:
    chain = LogChain(segments=(segment("s1", 0, 0), segment("s2", 0, None)))

    assert chain.active.virtual_start == 0


def test_segment_requires_sequencer_to_be_storage_member() -> None:
    with pytest.raises(ValidationError, match="storage member"):
        LogSegment(
            segment_id="s1",
            virtual_start=0,
            virtual_stop=None,
            loglet=native_loglet_configuration(
                ("db-1", "db-2", "db-3"),
                "db-4",
                "inc-s1",
            ),
        )


def test_segment_accepts_five_fixed_storage_members() -> None:
    members = ("db-1", "db-2", "db-3", "db-4", "db-5")

    observed = LogSegment(
        segment_id="s1",
        virtual_start=0,
        virtual_stop=None,
        loglet=native_loglet_configuration(
            members,
            "db-1",
            "inc-s1",
        ),
    )

    assert NativeLogletConfiguration.from_generic(observed.loglet).storage_members == members


def test_segment_accepts_even_storage_membership() -> None:
    segment = LogSegment(
        segment_id="s1",
        virtual_start=0,
        virtual_stop=None,
        loglet=native_loglet_configuration(
            ("db-1", "db-2", "db-3", "db-4"),
            "db-1",
            "inc-s1",
        ),
    )

    assert NativeLogletConfiguration.from_generic(segment.loglet).storage_members[-1] == "db-4"


def test_version_zero_is_the_only_snapshot_without_a_chain() -> None:
    assert VersionedLogChain(version=0, chain=None).chain is None

    with pytest.raises(ValidationError, match="version zero"):
        VersionedLogChain(version=1, chain=None)
