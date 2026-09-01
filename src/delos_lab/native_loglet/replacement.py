import asyncio

import httpx

from delos_lab.common.membership import quorum_size
from delos_lab.virtual_log.types import (
    LogletConfiguration,
    LogletConfigurationUpdate,
    LogSegment,
)

from .client import NativeLogletClient
from .config import NativeLogletConfiguration
from .errors import NoQuorum, TailUnavailable
from .transport import LogletTransport
from .types import LogEntry, LogServerState


class NativeLogletReplacementPreparer:
    """Prepare a sealed NativeLoglet configuration before reconfigModify.

    VirtualLog treats configurations as opaque. Data copy, trim propagation,
    and replacement validation therefore belong to the NativeLoglet adapter.
    """

    def __init__(self, transport: LogletTransport) -> None:
        self._transport = transport

    async def prepare(
        self,
        segment: LogSegment,
        replacement: LogletConfiguration,
    ) -> LogletConfigurationUpdate:
        if segment.virtual_stop is None:
            raise ValueError("NativeLoglet replacement requires a sealed segment")
        current = NativeLogletConfiguration.from_generic(segment.loglet)
        target = NativeLogletConfiguration.from_generic(replacement)
        length = segment.virtual_stop - segment.virtual_start

        current_client = NativeLogletClient(
            segment.segment_id,
            current.storage_members,
            self._transport,
        )
        current_tail = await current_client.check_tail()
        if not current_tail.sealed:
            raise TailUnavailable("NativeLoglet replacement source is not sealed")
        if current_tail.tail < length:
            raise TailUnavailable("NativeLoglet replacement source does not cover its range")

        trimmed_prefix = await self._quorum_trimmed_prefix(
            segment.segment_id,
            current.storage_members,
        )
        for position in range(trimmed_prefix, length):
            entry = await self._read_entry(
                segment.segment_id,
                position,
                current.storage_members,
                current_tail.tail,
            )
            await self._repair_quorum(entry, target.storage_members, current_tail.tail)

        target_client = NativeLogletClient(
            segment.segment_id,
            target.storage_members,
            self._transport,
        )
        target_client.observe_known_tail(current_tail.tail)
        if trimmed_prefix:
            confirmed = await target_client.prefix_trim(trimmed_prefix)
            if confirmed < trimmed_prefix:
                raise TailUnavailable("replacement trim watermark was not confirmed")
        await target_client.seal()
        target_tail = await target_client.check_tail()
        if not target_tail.sealed or target_tail.tail < length:
            raise TailUnavailable("replacement does not serve the sealed segment range")

        return LogletConfigurationUpdate(
            segment_id=segment.segment_id,
            loglet=replacement,
        )

    async def _quorum_trimmed_prefix(
        self,
        segment_id: str,
        members: tuple[str, ...],
    ) -> int:
        replies = await asyncio.gather(
            *(self._transport.state(node, segment_id) for node in members),
            return_exceptions=True,
        )
        states = [reply for reply in replies if isinstance(reply, LogServerState)]
        quorum = quorum_size(len(members))
        if len(states) < quorum:
            raise NoQuorum(segment_id)
        watermarks = sorted((state.trimmed_prefix for state in states), reverse=True)
        return watermarks[quorum - 1]

    async def _read_entry(
        self,
        segment_id: str,
        position: int,
        members: tuple[str, ...],
        known_tail: int,
    ) -> LogEntry:
        for node in members:
            try:
                entry = await self._transport.get(node, segment_id, position, known_tail)
            except KeyError, ConnectionError, httpx.HTTPError:
                continue
            if entry is not None:
                return entry
        raise TailUnavailable(f"replacement source is missing {segment_id}:{position}")

    async def _repair_quorum(
        self,
        entry: LogEntry,
        members: tuple[str, ...],
        known_tail: int,
    ) -> None:
        replies = await asyncio.gather(
            *(self._transport.repair(node, entry, known_tail) for node in members),
            return_exceptions=True,
        )
        if sum(isinstance(reply, LogServerState) for reply in replies) < quorum_size(len(members)):
            raise NoQuorum(entry.segment_id)
