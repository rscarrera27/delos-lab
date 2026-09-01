import asyncio
from typing import Self

from .errors import PaxosSafetyError
from .state_machine import StateMachineResult, VersionRegisterStateMachine
from .storage import PaxosStorage
from .types import (
    AcceptorSlotState,
    AcceptRequest,
    AcceptResponse,
    Ballot,
    DecideRequest,
    DecideResponse,
    PersistentPaxosState,
    PrepareRequest,
    PrepareResponse,
)


def replace_slot(
    state: PersistentPaxosState,
    changed: AcceptorSlotState,
) -> PersistentPaxosState:
    slots = [slot for slot in state.slots if slot.slot != changed.slot]
    slots.append(changed)
    slots.sort(key=lambda item: item.slot)
    return state.model_copy(update={"slots": tuple(slots)})


class PaxosAcceptor:
    def __init__(
        self,
        node_id: str,
        storage: PaxosStorage,
        state_machine: VersionRegisterStateMachine,
        state: PersistentPaxosState,
    ) -> None:
        self.node_id = node_id
        self._storage = storage
        self._state_machine = state_machine
        self._state = state
        self._state_machine.restore(state.state_machine)
        self._lock = asyncio.Lock()
        self._applied_results: dict[int, StateMachineResult] = {}

    @classmethod
    async def create(
        cls,
        node_id: str,
        storage: PaxosStorage,
        state_machine: VersionRegisterStateMachine,
    ) -> Self:
        return cls(node_id, storage, state_machine, await storage.load())

    @property
    def state(self) -> PersistentPaxosState:
        return self._state

    async def _save(self, state: PersistentPaxosState) -> None:
        await self._storage.save(state)
        self._state = state

    def _slot(self, slot_number: int) -> AcceptorSlotState:
        return next(
            (slot for slot in self._state.slots if slot.slot == slot_number),
            AcceptorSlotState(slot=slot_number),
        )

    async def reserve_ballot(self, minimum_round: int = 0) -> Ballot:
        async with self._lock:
            next_round = max(self._state.local_round + 1, minimum_round + 1)
            await self._save(self._state.model_copy(update={"local_round": next_round}))
            return Ballot(round=next_round, node_id=self.node_id)

    async def prepare(self, request: PrepareRequest) -> PrepareResponse:
        async with self._lock:
            slot = self._slot(request.slot)
            if slot.promised_ballot is not None and request.ballot < slot.promised_ballot:
                return PrepareResponse(
                    promised=False,
                    promised_ballot=slot.promised_ballot,
                    accepted_ballot=slot.accepted_ballot,
                    accepted_value=slot.accepted_value,
                    decided_value=slot.decided_value,
                )

            changed = slot.model_copy(update={"promised_ballot": request.ballot})
            await self._save(replace_slot(self._state, changed))
            return PrepareResponse(
                promised=True,
                promised_ballot=request.ballot,
                accepted_ballot=changed.accepted_ballot,
                accepted_value=changed.accepted_value,
                decided_value=changed.decided_value,
            )

    async def accept(self, request: AcceptRequest) -> AcceptResponse:
        async with self._lock:
            slot = self._slot(request.slot)
            if (
                slot.accepted_ballot == request.ballot
                and slot.accepted_value is not None
                and slot.accepted_value != request.value
            ):
                raise PaxosSafetyError(f"slot {request.slot} ballot was reused for another value")
            if slot.decided_value is not None and slot.decided_value != request.value:
                promised = slot.promised_ballot or request.ballot
                return AcceptResponse(accepted=False, promised_ballot=promised)
            if slot.promised_ballot is not None and request.ballot < slot.promised_ballot:
                return AcceptResponse(
                    accepted=False,
                    promised_ballot=slot.promised_ballot,
                )

            changed = slot.model_copy(
                update={
                    "promised_ballot": request.ballot,
                    "accepted_ballot": request.ballot,
                    "accepted_value": request.value,
                }
            )
            await self._save(replace_slot(self._state, changed))
            return AcceptResponse(accepted=True, promised_ballot=request.ballot)

    async def decide(self, request: DecideRequest) -> DecideResponse:
        async with self._lock:
            slot = self._slot(request.slot)
            if slot.decided_value is not None:
                if slot.decided_value != request.value:
                    raise PaxosSafetyError(f"slot {request.slot} already decided a different value")
                return DecideResponse()

            changed = slot.model_copy(update={"decided_value": request.value})
            state = replace_slot(self._state, changed)
            last_applied = state.last_applied
            pending_results: list[tuple[int, StateMachineResult]] = []
            previous_snapshot = self._state_machine.snapshot
            while True:
                next_slot_number = last_applied + 1
                next_slot = next(
                    (candidate for candidate in state.slots if candidate.slot == next_slot_number),
                    None,
                )
                if next_slot is None or next_slot.decided_value is None:
                    break
                last_applied = next_slot_number
                pending_results.append(
                    (
                        last_applied,
                        self._state_machine.apply(next_slot.decided_value.command),
                    )
                )

            state = state.model_copy(
                update={
                    "last_applied": last_applied,
                    "state_machine": self._state_machine.snapshot,
                }
            )
            try:
                await self._save(state)
            except BaseException:
                self._state_machine.restore(previous_snapshot)
                raise
            self._applied_results.update(pending_results)
            return DecideResponse()

    def result(self, slot: int) -> StateMachineResult:
        return self._applied_results[slot]
