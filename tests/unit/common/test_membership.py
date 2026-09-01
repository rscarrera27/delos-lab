import pytest

from delos_lab.common.membership import quorum_size, validate_fixed_members


@pytest.mark.parametrize(("count", "expected"), [(3, 2), (4, 3), (5, 3), (7, 4)])
def test_quorum_size(count: int, expected: int) -> None:
    assert quorum_size(count) == expected


@pytest.mark.parametrize(
    "members",
    [(), ("a",), ("a", "b")],
)
def test_fixed_members_reject_too_small_cluster(members: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="at least three"):
        validate_fixed_members(members, label="test")


def test_fixed_members_accept_even_majority_cluster() -> None:
    members = ("a", "b", "c", "d")

    assert validate_fixed_members(members, label="test") == members


def test_fixed_members_reject_duplicates() -> None:
    with pytest.raises(ValueError, match="unique non-empty"):
        validate_fixed_members(("a", "b", "a"), label="test")


def test_fixed_members_preserve_canonical_input_order() -> None:
    members = ("node-c", "node-a", "node-b")

    assert validate_fixed_members(members, label="test") == members
