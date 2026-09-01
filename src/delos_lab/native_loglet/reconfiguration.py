from collections.abc import Mapping
from typing import Protocol
from uuid import uuid4

import httpx

from delos_lab.common.membership import validate_fixed_members
from delos_lab.virtual_log.loglet import LogletUnavailable
from delos_lab.virtual_log.types import LogSegment, NewLogletConfiguration

from .config import NativeLogletConfiguration, native_loglet_configuration
from .endpoints import EndpointDirectory, endpoint_directory


class IncarnationDirectory(Protocol):
    async def incarnation(self, node_id: str) -> str | None: ...


class HttpIncarnationDirectory:
    def __init__(
        self,
        base_urls: Mapping[str, str] | EndpointDirectory,
        client: httpx.AsyncClient,
    ) -> None:
        self._endpoints = endpoint_directory(base_urls)
        self._client = client

    async def incarnation(self, node_id: str) -> str | None:
        try:
            response = await self._client.get(f"{self._endpoints.endpoint(node_id)}/health")
            response.raise_for_status()
        except KeyError, httpx.HTTPError:
            return None
        value = response.json().get("incarnation_id")
        return value if isinstance(value, str) and value else None


class NativeLogletReconfigurationPolicy:
    """Build NativeLoglet configs without leaking their schema into KV or VirtualLog."""

    def __init__(
        self,
        members: tuple[str, ...],
        incarnations: IncarnationDirectory,
    ) -> None:
        self._initial_members = validate_fixed_members(members, label="NativeLoglet storage")
        self._incarnations = incarnations

    async def initial_segment(self) -> LogSegment:
        sequencer = self._initial_members[0]
        incarnation = await self._incarnations.incarnation(sequencer)
        if incarnation is None:
            raise LogletUnavailable(f"initial sequencer {sequencer} is unavailable")
        return LogSegment(
            segment_id=str(uuid4()),
            virtual_start=0,
            virtual_stop=None,
            loglet=native_loglet_configuration(
                self._initial_members,
                sequencer,
                incarnation,
            ),
        )

    async def successor(self, failed: LogSegment) -> NewLogletConfiguration:
        try:
            configuration = NativeLogletConfiguration.from_generic(failed.loglet)
        except ValueError as error:
            raise LogletUnavailable(str(error)) from error
        members = configuration.storage_members
        try:
            start = members.index(configuration.sequencer_node)
        except ValueError:
            start = -1
        for offset in range(1, len(members) + 1):
            node_id = members[(start + offset) % len(members)]
            if node_id == configuration.sequencer_node:
                continue
            incarnation = await self._incarnations.incarnation(node_id)
            if incarnation is not None:
                return NewLogletConfiguration(
                    segment_id=str(uuid4()),
                    loglet=native_loglet_configuration(
                        members,
                        node_id,
                        incarnation,
                    ),
                )
        raise LogletUnavailable("no live NativeLoglet sequencer candidate")
