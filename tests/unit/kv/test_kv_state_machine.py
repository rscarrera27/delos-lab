import pytest
from pydantic import ValidationError

from delos_lab.kv.state_machine import KvStateMachine
from delos_lab.kv.types import CompareAndSet, Delete, Increment, KvCommandEnvelope, Put


def test_put_cas_increment_and_delete() -> None:
    machine = KvStateMachine()

    assert machine.apply(Put(key="n", value=3)).value == 3
    assert machine.apply(CompareAndSet(key="n", expected=3, value=4)).code == "APPLIED"
    assert machine.apply(Increment(key="n", delta=2)).value == 6
    assert machine.apply(Delete(key="n")).value == 6
    assert machine.get("n") is None


def test_command_schema_rejects_removed_read_barrier() -> None:
    with pytest.raises(ValidationError):
        KvCommandEnvelope.model_validate(
            {
                "client_id": "reader",
                "request_id": "old",
                "operation": {"kind": "read_barrier"},
            }
        )


def test_increment_string_returns_deterministic_type_mismatch() -> None:
    machine = KvStateMachine({"name": "delos"})

    result = machine.apply(Increment(key="name", delta=1))

    assert result.code == "TYPE_MISMATCH"
    assert machine.get("name") == "delos"


def test_compare_and_set_treats_missing_key_as_none() -> None:
    machine = KvStateMachine()

    applied = machine.apply(CompareAndSet(key="missing", expected=None, value="created"))
    mismatch = machine.apply(CompareAndSet(key="missing", expected=None, value="other"))

    assert applied.value == "created"
    assert mismatch.code == "CAS_MISMATCH"
    assert mismatch.value == "created"
