import pytest
from pydantic import ValidationError

from delos_lab.metastore.paxos.types import (
    AcceptorSlotState,
    Ballot,
    PersistentPaxosState,
    PrepareResponse,
)


def test_ballots_have_a_total_lexicographic_order() -> None:
    assert Ballot(round=1, node_id="meta-2") < Ballot(round=2, node_id="meta-1")
    assert Ballot(round=2, node_id="meta-1") < Ballot(round=2, node_id="meta-2")
    assert max(
        Ballot(round=2, node_id="meta-1"),
        Ballot(round=2, node_id="meta-2"),
    ) == Ballot(round=2, node_id="meta-2")


def test_accepted_ballot_and_value_must_appear_together() -> None:
    with pytest.raises(ValidationError):
        AcceptorSlotState(
            slot=1,
            accepted_ballot=Ballot(round=1, node_id="meta-1"),
        )


def test_prepare_response_accepted_pair_must_appear_together() -> None:
    with pytest.raises(ValidationError):
        PrepareResponse(
            promised=True,
            promised_ballot=Ballot(round=1, node_id="meta-1"),
            accepted_ballot=Ballot(round=1, node_id="meta-1"),
        )


def test_applied_slots_must_have_contiguous_decisions() -> None:
    with pytest.raises(ValidationError):
        PersistentPaxosState(last_applied=1)


def test_slot_states_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValidationError):
        PersistentPaxosState(slots=(AcceptorSlotState(slot=2), AcceptorSlotState(slot=1)))
    with pytest.raises(ValidationError):
        PersistentPaxosState(slots=(AcceptorSlotState(slot=1), AcceptorSlotState(slot=1)))
