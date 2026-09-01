import pytest
from pydantic import ValidationError

from delos_lab.kv.types import Increment, KvCommandEnvelope, Put


def test_command_id_and_payload_are_stable() -> None:
    command = KvCommandEnvelope(
        client_id="client/a",
        request_id="request/1",
        operation=Put(key="count", value=1),
    )

    restored = KvCommandEnvelope.from_payload(command.to_payload())

    assert command.command_id == "client%2Fa/request%2F1"
    assert restored == command


def test_increment_rejects_boolean_delta() -> None:
    with pytest.raises(ValidationError):
        Increment(key="count", delta=True)
