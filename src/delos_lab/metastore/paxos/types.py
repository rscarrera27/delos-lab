from functools import total_ordering
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from delos_lab.virtual_log.types import LogChain, VersionedLogChain

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]


@total_ordering
class Ballot(BaseModel):
    model_config = ConfigDict(frozen=True)

    round: PositiveInt
    node_id: str = Field(min_length=1)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Ballot):
            return NotImplemented
        return (self.round, self.node_id) < (other.round, other.node_id)


class CompareAndSetCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["compare_and_set"] = "compare_and_set"
    expected_version: NonNegativeInt
    new_chain: LogChain


class ReadBarrierCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["read_barrier"] = "read_barrier"


type PaxosCommand = Annotated[
    CompareAndSetCommand | ReadBarrierCommand,
    Field(discriminator="kind"),
]


class PaxosValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    command: PaxosCommand


class AcceptorSlotState(BaseModel):
    model_config = ConfigDict(frozen=True)

    slot: PositiveInt
    promised_ballot: Ballot | None = None
    accepted_ballot: Ballot | None = None
    accepted_value: PaxosValue | None = None
    decided_value: PaxosValue | None = None

    @model_validator(mode="after")
    def validate_accepted_pair(self) -> Self:
        if (self.accepted_ballot is None) != (self.accepted_value is None):
            raise ValueError("accepted ballot and value must appear together")
        if (
            self.accepted_ballot is not None
            and self.promised_ballot is not None
            and self.accepted_ballot > self.promised_ballot
        ):
            raise ValueError("accepted ballot cannot exceed promised ballot")
        return self


class PersistentPaxosState(BaseModel):
    model_config = ConfigDict(frozen=True)

    local_round: NonNegativeInt = 0
    slots: tuple[AcceptorSlotState, ...] = ()
    last_applied: NonNegativeInt = 0
    state_machine: VersionedLogChain = Field(
        default_factory=lambda: VersionedLogChain(version=0, chain=None)
    )

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        slot_numbers = tuple(slot.slot for slot in self.slots)
        if slot_numbers != tuple(sorted(slot_numbers)):
            raise ValueError("slot states must be sorted")
        if len(set(slot_numbers)) != len(slot_numbers):
            raise ValueError("slot states must be unique")
        decisions = {slot.slot for slot in self.slots if slot.decided_value is not None}
        if any(slot not in decisions for slot in range(1, self.last_applied + 1)):
            raise ValueError("all applied slots must be decided")
        return self


class PrepareRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    slot: PositiveInt
    ballot: Ballot


class PrepareResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    promised: bool
    promised_ballot: Ballot
    accepted_ballot: Ballot | None = None
    accepted_value: PaxosValue | None = None
    decided_value: PaxosValue | None = None

    @model_validator(mode="after")
    def validate_accepted_pair(self) -> Self:
        if (self.accepted_ballot is None) != (self.accepted_value is None):
            raise ValueError("accepted ballot and value must appear together")
        return self


class AcceptRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    slot: PositiveInt
    ballot: Ballot
    value: PaxosValue


class AcceptResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: bool
    promised_ballot: Ballot


class DecideRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    slot: PositiveInt
    value: PaxosValue


class DecideResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    learned: bool = True
