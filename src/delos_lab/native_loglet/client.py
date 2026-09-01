import asyncio

import httpx

from delos_lab.common.membership import quorum_size, validate_fixed_members

from .errors import EntryConflict, NoQuorum, TailUnavailable
from .transport import LogletTransport
from .types import CheckTailResult, LogEntry, LogServerState


class NativeLogletClient:
    def __init__(
        self,
        segment_id: str,
        members: tuple[str, ...],
        transport: LogletTransport,
    ) -> None:
        self.segment_id = segment_id
        self.members = validate_fixed_members(members, label="native Loglet storage")
        self.transport = transport
        self.quorum = quorum_size(len(self.members))
        self._known_tail = 0
        self._trimmed_prefix = 0

    @property
    def known_tail(self) -> int:
        return self._known_tail

    @property
    def trimmed_prefix(self) -> int:
        return self._trimmed_prefix

    def observe_known_tail(self, tail: int) -> None:
        self._known_tail = max(self._known_tail, tail)

    async def seal(self) -> None:
        replies = await asyncio.gather(
            *(
                self.transport.seal(node, self.segment_id, self._known_tail)
                for node in self.members
            ),
            return_exceptions=True,
        )
        states = [reply for reply in replies if isinstance(reply, LogServerState)]
        for state in states:
            self.observe_known_tail(state.known_tail)
        if len(states) < self.quorum:
            raise NoQuorum(self.segment_id)

    async def prefix_trim(self, trim_position: int) -> int:
        if trim_position < 0:
            raise ValueError("prefixTrim requires a non-negative position")
        replies = await asyncio.gather(
            *(
                self.transport.prefix_trim(node, self.segment_id, trim_position)
                for node in self.members
            ),
            return_exceptions=True,
        )
        states = [reply for reply in replies if isinstance(reply, LogServerState)]
        if len(states) < self.quorum:
            raise NoQuorum(self.segment_id)
        watermark = min(state.trimmed_prefix for state in states)
        self._trimmed_prefix = max(self._trimmed_prefix, watermark)
        return watermark

    async def check_tail(self) -> CheckTailResult:
        """Determine global tail and seal state from a responding majority.

        In the none-sealed state, wait on the paper's LogServer notification
        API instead of turning propagation delay into a bounded polling error.
        A tail already stored on a quorum is direct commitment evidence.
        """
        while True:
            states = await self._read_states()
            self._trimmed_prefix = max(
                self._trimmed_prefix,
                *(state.trimmed_prefix for _, state in states),
            )
            sealed_count = sum(state.sealed for _, state in states)

            if 0 < sealed_count < len(states):
                await self.seal()
                continue

            tail = max(state.local_tail for _, state in states)
            if sealed_count == len(states):
                await self._repair_through(tail, tuple(node for node, _ in states))
                self.observe_known_tail(tail)
                return CheckTailResult(tail=tail, sealed=True)

            copies_at_tail = sum(state.local_tail >= tail for _, state in states)
            if tail == 0 or self._known_tail >= tail or copies_at_tail >= self.quorum:
                self.observe_known_tail(tail)
                return CheckTailResult(tail=tail, sealed=False)

            await self._wait_for_progress(states, tail)

    async def _wait_for_progress(
        self,
        states: list[tuple[str, LogServerState]],
        observed_tail: int,
    ) -> None:
        """Wait for one useful local-tail or seal notification.

        A server already at the maximum tail is asked for the following local
        position; a trailing server is asked to catch up to the maximum. This
        avoids an immediate notification loop while retaining the paper's
        local-tail-or-seal wake-up contract.
        """
        observed = dict(states)
        tasks: set[asyncio.Task[LogServerState]] = set()
        for node in self.members:
            state = observed.get(node)
            target = (
                observed_tail
                if state is None or state.local_tail < observed_tail
                else state.local_tail + 1
            )
            tasks.add(
                asyncio.create_task(
                    self.transport.wait_for_tail(
                        node,
                        self.segment_id,
                        target,
                        self._known_tail,
                    )
                )
            )

        all_tasks = set(tasks)
        try:
            while tasks:
                done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    try:
                        state = task.result()
                    except ConnectionError, httpx.HTTPError:
                        continue
                    self.observe_known_tail(state.known_tail)
                    return
            raise NoQuorum(self.segment_id)
        finally:
            for task in all_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*all_tasks, return_exceptions=True)

    async def _read_states(self) -> list[tuple[str, LogServerState]]:
        replies = await asyncio.gather(
            *(
                self.transport.state(node, self.segment_id, self._known_tail)
                for node in self.members
            ),
            return_exceptions=True,
        )
        states = [
            (node, reply)
            for node, reply in zip(self.members, replies, strict=True)
            if isinstance(reply, LogServerState)
        ]
        if len(states) < self.quorum:
            raise NoQuorum(self.segment_id)
        for _, state in states:
            self.observe_known_tail(state.known_tail)
        return states

    async def _repair_through(self, tail: int, nodes: tuple[str, ...]) -> None:
        for position in range(self._trimmed_prefix, tail):
            copies: list[LogEntry] = []
            for node in nodes:
                entry = await self.transport.get(node, self.segment_id, position, self._known_tail)
                if entry is not None:
                    copies.append(entry)

            if not copies:
                raise TailUnavailable(f"missing position {position}")
            canonical = copies[0]
            if any(copy != canonical for copy in copies[1:]):
                raise EntryConflict(f"conflicting position {position}")

            for node in nodes:
                entry = await self.transport.get(node, self.segment_id, position, self._known_tail)
                if entry is None:
                    await self.transport.repair(node, canonical, self._known_tail)
