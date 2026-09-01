from collections.abc import Mapping
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from delos_lab.virtual_log.loglet import (
    LogletAppend,
    LogletEntry,
    LogletSealed,
    LogletTail,
    LogletUnavailable,
    VirtualLoglet,
)
from delos_lab.virtual_log.types import LogSegment

from .client import NativeLogletClient
from .config import NativeLogletConfiguration
from .endpoints import EndpointDirectory, endpoint_directory
from .errors import (
    EntryConflict,
    IncarnationMismatch,
    NoQuorum,
    NotSequencer,
    SegmentSealed,
    SequencerUnavailable,
    TailUnavailable,
)
from .sequencer import NativeSequencer
from .transport import LogletTransport
from .types import AppendResult


class SequencerAppendRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment: LogSegment
    command_id: str
    payload: bytes


class SequencerTransport(Protocol):
    async def append(
        self,
        node_id: str,
        segment: LogSegment,
        command_id: str,
        payload: bytes,
    ) -> AppendResult: ...


class NativeLogletRuntime(VirtualLoglet):
    """In-process adapter used by focused VirtualLog tests and demos."""

    def __init__(
        self,
        sequencer: NativeSequencer,
        client: NativeLogletClient,
        transport: LogletTransport,
        members: tuple[str, ...],
    ) -> None:
        self._sequencer = sequencer
        self._client = client
        self._transport = transport
        self._members = members
        self._last_check_tail: LogletTail | None = None

    @property
    def known_tail(self) -> int:
        return self._client.known_tail

    @property
    def last_check_tail(self) -> LogletTail | None:
        return self._last_check_tail

    async def append(self, command_id: str, payload: bytes) -> LogletAppend:
        try:
            result = await self._sequencer.append(command_id, payload)
        except SegmentSealed as error:
            raise LogletSealed(str(error)) from error
        except (NoQuorum, SequencerUnavailable, NotSequencer, IncarnationMismatch) as error:
            raise LogletUnavailable(str(error)) from error
        self._client.observe_known_tail(result.known_tail)
        return LogletAppend(position=result.position, known_tail=result.known_tail)

    async def seal(self) -> None:
        try:
            await self._client.seal()
        except (NoQuorum, TailUnavailable) as error:
            raise LogletUnavailable(str(error)) from error

    async def check_tail(self) -> LogletTail:
        try:
            result = await self._client.check_tail()
        except (NoQuorum, TailUnavailable) as error:
            raise LogletUnavailable(str(error)) from error
        observed = LogletTail(tail=result.tail, sealed=result.sealed)
        self._last_check_tail = observed
        return observed

    async def prefix_trim(self, trim_position: int) -> int:
        try:
            return await self._client.prefix_trim(trim_position)
        except NoQuorum as error:
            raise LogletUnavailable(str(error)) from error

    async def read_next(self, local_start: int, local_stop: int) -> LogletEntry | None:
        effective_start = max(local_start, self._client.trimmed_prefix)
        if effective_start >= local_stop:
            return None
        return await _read_next_from_members(
            self._members,
            self._transport,
            self._client.segment_id,
            effective_start,
            local_stop,
            self._client.known_tail,
        )


class RemoteNativeLogletRuntime(VirtualLoglet):
    def __init__(
        self,
        local_node_id: str,
        segment: LogSegment,
        loglet_transport: LogletTransport,
        sequencer_transport: SequencerTransport,
    ) -> None:
        self._local_node_id = local_node_id
        self.segment = segment
        self.configuration = NativeLogletConfiguration.from_generic(segment.loglet)
        self._loglet_transport = loglet_transport
        self._sequencer_transport = sequencer_transport
        self._client = NativeLogletClient(
            segment.segment_id,
            self.configuration.storage_members,
            loglet_transport,
        )
        self._last_check_tail: LogletTail | None = None

    @property
    def known_tail(self) -> int:
        return self._client.known_tail

    @property
    def last_check_tail(self) -> LogletTail | None:
        return self._last_check_tail

    def observe_known_tail(self, tail: int) -> None:
        self._client.observe_known_tail(tail)

    async def append(self, command_id: str, payload: bytes) -> LogletAppend:
        try:
            result = await self._sequencer_transport.append(
                self.configuration.sequencer_node,
                self.segment,
                command_id,
                payload,
            )
        except SegmentSealed as error:
            raise LogletSealed(str(error)) from error
        except (NoQuorum, SequencerUnavailable, NotSequencer, IncarnationMismatch) as error:
            raise LogletUnavailable(str(error)) from error
        self._client.observe_known_tail(result.known_tail)
        return LogletAppend(position=result.position, known_tail=result.known_tail)

    async def seal(self) -> None:
        try:
            await self._client.seal()
        except (NoQuorum, TailUnavailable) as error:
            raise LogletUnavailable(str(error)) from error

    async def check_tail(self) -> LogletTail:
        try:
            result = await self._client.check_tail()
        except (NoQuorum, TailUnavailable) as error:
            raise LogletUnavailable(str(error)) from error
        observed = LogletTail(tail=result.tail, sealed=result.sealed)
        self._last_check_tail = observed
        return observed

    async def prefix_trim(self, trim_position: int) -> int:
        try:
            return await self._client.prefix_trim(trim_position)
        except NoQuorum as error:
            raise LogletUnavailable(str(error)) from error

    async def read_next(self, local_start: int, local_stop: int) -> LogletEntry | None:
        effective_start = max(local_start, self._client.trimmed_prefix)
        if effective_start >= local_stop:
            return None
        return await _read_next_from_members(
            self._read_members(),
            self._loglet_transport,
            self.segment.segment_id,
            effective_start,
            local_stop,
            self._client.known_tail,
        )

    def _read_members(self) -> tuple[str, ...]:
        return (
            self._local_node_id,
            *(
                member
                for member in self.configuration.storage_members
                if member != self._local_node_id
            ),
        )


async def _read_next_from_members(
    members: tuple[str, ...],
    transport: LogletTransport,
    segment_id: str,
    local_start: int,
    local_stop: int,
    known_tail: int,
) -> LogletEntry | None:
    """Locate the first NativeLoglet entry without inventing a hole.

    NativeLoglet has a dense global prefix, although an individual LogServer
    may omit entries it knows are globally committed. A missing position is
    therefore skippable only after every configured member answered; otherwise
    the absent copy may be on an unreachable member.
    """
    if local_start < 0 or local_stop <= local_start:
        raise ValueError("readNext requires 0 <= local_start < local_stop")
    for position in range(local_start, local_stop):
        responses = 0
        last_error: Exception | None = None
        for member in members:
            try:
                entry = await transport.get(member, segment_id, position, known_tail)
            except (ConnectionError, httpx.TransportError) as error:
                last_error = error
                continue
            responses += 1
            if entry is not None:
                return LogletEntry(
                    position=entry.position,
                    command_id=entry.command_id,
                    payload=entry.payload,
                )
        if responses != len(members):
            raise LogletUnavailable(
                f"cannot establish whether {segment_id}:{position} is present"
            ) from last_error
    return None


class HttpNativeLogletProvider:
    def __init__(
        self,
        local_node_id: str,
        loglet_transport: LogletTransport,
        sequencer_transport: SequencerTransport,
    ) -> None:
        self._local_node_id = local_node_id
        self._loglet_transport = loglet_transport
        self._sequencer_transport = sequencer_transport
        self._runtimes: dict[str, RemoteNativeLogletRuntime] = {}

    def get(self, segment: LogSegment) -> RemoteNativeLogletRuntime:
        runtime = self._runtimes.get(segment.segment_id)
        if runtime is None:
            try:
                runtime = RemoteNativeLogletRuntime(
                    self._local_node_id,
                    segment,
                    self._loglet_transport,
                    self._sequencer_transport,
                )
            except ValueError as error:
                raise LogletUnavailable(str(error)) from error
            self._runtimes[segment.segment_id] = runtime
        elif runtime.segment.loglet != segment.loglet:
            if segment.virtual_stop is None:
                raise LogletUnavailable(
                    f"active segment {segment.segment_id} configuration changed"
                )
            try:
                runtime = RemoteNativeLogletRuntime(
                    self._local_node_id,
                    segment,
                    self._loglet_transport,
                    self._sequencer_transport,
                )
            except ValueError as error:
                raise LogletUnavailable(str(error)) from error
            self._runtimes[segment.segment_id] = runtime
        return runtime

    def peek(self, segment: LogSegment) -> RemoteNativeLogletRuntime | None:
        runtime = self._runtimes.get(segment.segment_id)
        if runtime is None or runtime.segment.loglet != segment.loglet:
            return None
        return runtime


class HttpSequencerTransport:
    def __init__(
        self,
        base_urls: Mapping[str, str] | EndpointDirectory,
        client: httpx.AsyncClient,
    ) -> None:
        self._endpoints = endpoint_directory(base_urls)
        self._client = client

    async def append(
        self,
        node_id: str,
        segment: LogSegment,
        command_id: str,
        payload: bytes,
    ) -> AppendResult:
        request = SequencerAppendRequest(
            segment=segment,
            command_id=command_id,
            payload=payload,
        )
        try:
            response = await self._client.post(
                f"{self._endpoints.endpoint(node_id)}/internal/segments/{segment.segment_id}/append",
                json=request.model_dump(mode="json"),
            )
        except (KeyError, ConnectionError, httpx.HTTPError) as error:
            raise SequencerUnavailable(node_id) from error
        if response.status_code == 409:
            code = response.json().get("code")
            if code == "NOT_SEQUENCER":
                raise NotSequencer(node_id)
            if code == "INCARNATION_MISMATCH":
                raise IncarnationMismatch(node_id)
            if code == "SEALED":
                raise SegmentSealed(segment.segment_id)
            if code == "ENTRY_CONFLICT":
                raise EntryConflict(command_id)
        if response.status_code == 503:
            raise NoQuorum(segment.segment_id)
        try:
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise SequencerUnavailable(node_id) from error
        return AppendResult.model_validate(response.json())
