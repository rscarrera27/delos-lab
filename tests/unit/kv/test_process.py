import pytest

from delos_lab.runtime.converged_process import parse_mapping


def test_parse_mapping_accepts_fixed_majority_membership() -> None:
    values = (
        "db-1=http://127.0.0.1:1",
        "db-2=http://127.0.0.1:2",
        "db-3=http://127.0.0.1:3",
    )

    five = (*values, "db-4=http://127.0.0.1:4", "db-5=http://127.0.0.1:5")

    assert tuple(parse_mapping(values, label="DB")) == ("db-1", "db-2", "db-3")
    assert tuple(parse_mapping(five, label="DB")) == (
        "db-1",
        "db-2",
        "db-3",
        "db-4",
        "db-5",
    )
    assert len(parse_mapping(values + ("db-4=http://127.0.0.1:4",), label="DB")) == 4
    with pytest.raises(ValueError, match="at least three"):
        parse_mapping(values[:1], label="DB")


def test_parse_mapping_rejects_duplicate_or_malformed_values() -> None:
    with pytest.raises(ValueError, match="invalid DB mapping"):
        parse_mapping(("db-1",), label="DB")
    with pytest.raises(ValueError, match="unique non-empty"):
        parse_mapping(
            (
                "db-1=http://one",
                "db-1=http://another",
                "db-2=http://two",
            ),
            label="DB",
        )
