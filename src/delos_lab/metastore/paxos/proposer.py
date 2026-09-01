import asyncio

from delos_lab.common.membership import quorum_size, validate_fixed_members

from .acceptor import PaxosAcceptor
from .errors import PaxosNoQuorum, PaxosSafetyError
from .transport import PaxosTransport
from .types import (
    AcceptRequest,
    AcceptResponse,
    DecideRequest,
    PaxosValue,
    PrepareRequest,
    PrepareResponse,
)


class PaxosProposer:
    """Unoptimized Classic Paxos slots for the MetaStore (paper section 4.1).

    This lab deliberately fixes membership and has no stable Multi-Paxos
    leader. It must not be confused with a NativeLoglet sequencer.
    """

    def __init__(
        self,
        node_id: str,
        members: tuple[str, ...],
        acceptor: PaxosAcceptor,
        transport: PaxosTransport,
        *,
        max_ballot_attempts: int = 8,
    ) -> None:
        members = validate_fixed_members(members, label="Paxos")
        if node_id not in members:
            raise ValueError("Paxos membership must include this node")
        if max_ballot_attempts < 1:
            raise ValueError("max ballot attempts must be positive")
        self.node_id = node_id
        self.members = members
        self.quorum = quorum_size(len(members))
        self._acceptor = acceptor
        self._transport = transport
        self._max_ballot_attempts = max_ballot_attempts

    @staticmethod
    def _single_decision(responses: list[PrepareResponse]) -> PaxosValue | None:
        decisions = [
            response.decided_value for response in responses if response.decided_value is not None
        ]
        if not decisions:
            return None
        first = decisions[0]
        if any(decision != first for decision in decisions[1:]):
            raise PaxosSafetyError("acceptors reported conflicting decisions")
        return first

    @staticmethod
    def _adopted_value(
        responses: list[PrepareResponse],
        proposed: PaxosValue,
    ) -> PaxosValue:
        accepted = [
            (response.accepted_ballot, response.accepted_value)
            for response in responses
            if response.accepted_ballot is not None and response.accepted_value is not None
        ]
        if not accepted:
            return proposed
        return max(accepted, key=lambda item: item[0])[1]

    async def _learn(self, slot: int, value: PaxosValue) -> None:
        request = DecideRequest(slot=slot, value=value)
        await self._acceptor.decide(request)
        peers = tuple(member for member in self.members if member != self.node_id)
        await asyncio.gather(
            *(self._transport.decide(self.node_id, peer, request) for peer in peers),
            return_exceptions=True,
        )

    async def _decide_slot(self, slot: int, proposed: PaxosValue) -> PaxosValue:
        observed_round = 0
        for _ in range(self._max_ballot_attempts):
            ballot = await self._acceptor.reserve_ballot(observed_round)
            prepare_request = PrepareRequest(slot=slot, ballot=ballot)
            prepare_replies = await asyncio.gather(
                *(
                    self._transport.prepare(
                        self.node_id,
                        member,
                        prepare_request,
                    )
                    for member in self.members
                ),
                return_exceptions=True,
            )
            responses = [reply for reply in prepare_replies if isinstance(reply, PrepareResponse)]
            if len(responses) < self.quorum:
                raise PaxosNoQuorum(f"slot {slot} Prepare did not reach a quorum")
            observed_round = max(
                observed_round,
                *(response.promised_ballot.round for response in responses),
            )

            decided = self._single_decision(responses)
            if decided is not None:
                await self._learn(slot, decided)
                return decided

            promised = [response for response in responses if response.promised]
            if len(promised) < self.quorum:
                continue
            selected = self._adopted_value(promised, proposed)
            accept_request = AcceptRequest(
                slot=slot,
                ballot=ballot,
                value=selected,
            )
            accept_replies = await asyncio.gather(
                *(
                    self._transport.accept(
                        self.node_id,
                        member,
                        accept_request,
                    )
                    for member in self.members
                ),
                return_exceptions=True,
            )
            accept_responses = [
                reply for reply in accept_replies if isinstance(reply, AcceptResponse)
            ]
            if len(accept_responses) < self.quorum:
                raise PaxosNoQuorum(f"slot {slot} Accept did not reach a quorum")
            observed_round = max(
                observed_round,
                *(response.promised_ballot.round for response in accept_responses),
            )
            if sum(response.accepted for response in accept_responses) < self.quorum:
                continue

            await self._learn(slot, selected)
            return selected

        raise PaxosNoQuorum(f"slot {slot} did not decide after {self._max_ballot_attempts} ballots")

    async def propose(self, value: PaxosValue) -> tuple[int, PaxosValue]:
        while True:
            slot = self._acceptor.state.last_applied + 1
            decided = await self._decide_slot(slot, value)
            if decided == value:
                return slot, decided
