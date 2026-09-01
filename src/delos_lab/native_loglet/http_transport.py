from collections.abc import Mapping

import httpx

from .endpoints import EndpointDirectory, endpoint_directory
from .errors import EntryConflict, PositionTrimmed, PredecessorUnavailable, SegmentSealed
from .types import KnownTailRequest, LogEntry, LogletWriteRequest, LogServerState


class HttpLogletTransport:
    def __init__(
        self,
        base_urls: Mapping[str, str] | EndpointDirectory,
        client: httpx.AsyncClient,
    ) -> None:
        self._endpoints = endpoint_directory(base_urls)
        self._client = client

    def _url(self, node_id: str, path: str) -> str:
        return f"{self._endpoints.endpoint(node_id)}{path}"

    @staticmethod
    def _raise_for_protocol_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        if response.status_code == 409:
            body = response.json()
            code = body.get("code")
            message = body.get("message", code)
            if code == "SEALED":
                raise SegmentSealed(message)
            if code == "ENTRY_CONFLICT":
                raise EntryConflict(message)
            if code == "PREDECESSOR_UNAVAILABLE":
                raise PredecessorUnavailable(message)
            if code == "TRIMMED":
                raise PositionTrimmed(message)
        response.raise_for_status()

    async def put(self, node_id: str, entry: LogEntry, known_tail: int = 0) -> LogServerState:
        response = await self._client.put(
            self._url(node_id, f"/segments/{entry.segment_id}/entries/{entry.position}"),
            json=LogletWriteRequest(entry=entry, known_tail=known_tail).model_dump(mode="json"),
        )
        self._raise_for_protocol_error(response)
        return LogServerState.model_validate(response.json())

    async def repair(self, node_id: str, entry: LogEntry, known_tail: int = 0) -> LogServerState:
        response = await self._client.put(
            self._url(node_id, f"/segments/{entry.segment_id}/repairs/{entry.position}"),
            json=LogletWriteRequest(entry=entry, known_tail=known_tail).model_dump(mode="json"),
        )
        self._raise_for_protocol_error(response)
        return LogServerState.model_validate(response.json())

    async def get(
        self, node_id: str, segment_id: str, position: int, known_tail: int = 0
    ) -> LogEntry | None:
        response = await self._client.get(
            self._url(node_id, f"/segments/{segment_id}/entries/{position}"),
            params={"known_tail": known_tail},
        )
        if response.status_code == 404:
            return None
        self._raise_for_protocol_error(response)
        return LogEntry.model_validate(response.json())

    async def seal(self, node_id: str, segment_id: str, known_tail: int = 0) -> LogServerState:
        response = await self._client.post(
            self._url(node_id, f"/segments/{segment_id}/seal"),
            json=KnownTailRequest(known_tail=known_tail).model_dump(mode="json"),
        )
        self._raise_for_protocol_error(response)
        return LogServerState.model_validate(response.json())

    async def prefix_trim(
        self, node_id: str, segment_id: str, trim_position: int
    ) -> LogServerState:
        response = await self._client.post(
            self._url(node_id, f"/segments/{segment_id}/prefix-trim/{trim_position}")
        )
        self._raise_for_protocol_error(response)
        return LogServerState.model_validate(response.json())

    async def state(self, node_id: str, segment_id: str, known_tail: int = 0) -> LogServerState:
        response = await self._client.get(
            self._url(node_id, f"/segments/{segment_id}/state"),
            params={"known_tail": known_tail},
        )
        self._raise_for_protocol_error(response)
        return LogServerState.model_validate(response.json())

    async def wait_for_tail(
        self,
        node_id: str,
        segment_id: str,
        local_tail: int,
        known_tail: int = 0,
    ) -> LogServerState:
        response = await self._client.get(
            self._url(node_id, f"/segments/{segment_id}/tail-notifications/{local_tail}"),
            params={"known_tail": known_tail},
            timeout=None,
        )
        self._raise_for_protocol_error(response)
        return LogServerState.model_validate(response.json())
