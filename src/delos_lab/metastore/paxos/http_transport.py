from collections.abc import Mapping

import httpx

from delos_lab.virtual_log.metastore import Applied, CompareAndSetResult, VersionMismatch
from delos_lab.virtual_log.types import LogChain, VersionedLogChain

from .errors import PaxosNoQuorum
from .transport import PaxosTransport
from .types import (
    AcceptRequest,
    AcceptResponse,
    DecideRequest,
    DecideResponse,
    PrepareRequest,
    PrepareResponse,
)


class HttpPaxosTransport(PaxosTransport):
    def __init__(
        self,
        base_urls: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> None:
        self._base_urls = {node_id: base_url.rstrip("/") for node_id, base_url in base_urls.items()}
        self._client = client

    def _url(self, target: str, path: str) -> str:
        try:
            base_url = self._base_urls[target]
        except KeyError as error:
            raise ConnectionError(f"unknown Paxos peer: {target}") from error
        return f"{base_url}{path}"

    async def prepare(
        self,
        source: str,
        target: str,
        request: PrepareRequest,
    ) -> PrepareResponse:
        try:
            response = await self._client.post(
                self._url(target, "/paxos/prepare"),
                json=request.model_dump(mode="json"),
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ConnectionError(f"Prepare to {target} failed") from error
        return PrepareResponse.model_validate(response.json())

    async def accept(
        self,
        source: str,
        target: str,
        request: AcceptRequest,
    ) -> AcceptResponse:
        try:
            response = await self._client.post(
                self._url(target, "/paxos/accept"),
                json=request.model_dump(mode="json"),
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ConnectionError(f"Accept to {target} failed") from error
        return AcceptResponse.model_validate(response.json())

    async def decide(
        self,
        source: str,
        target: str,
        request: DecideRequest,
    ) -> DecideResponse:
        try:
            response = await self._client.post(
                self._url(target, "/paxos/decide"),
                json=request.model_dump(mode="json"),
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ConnectionError(f"Decide to {target} failed") from error
        return DecideResponse.model_validate(response.json())


class HttpMetaStorePeer:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    @staticmethod
    def _raise_protocol_error(response: httpx.Response) -> None:
        if response.status_code == 503:
            raise PaxosNoQuorum("MetaStore peer did not reach a quorum")
        response.raise_for_status()

    async def read(self) -> VersionedLogChain:
        try:
            response = await self._client.get(f"{self._base_url}/metastore")
            self._raise_protocol_error(response)
        except httpx.HTTPError as error:
            raise ConnectionError("MetaStore read failed") from error
        return VersionedLogChain.model_validate(response.json())

    async def compare_and_set(
        self,
        expected_version: int,
        new_chain: LogChain,
    ) -> CompareAndSetResult:
        try:
            response = await self._client.post(
                f"{self._base_url}/metastore/compare-and-set",
                json={
                    "expected_version": expected_version,
                    "new_chain": new_chain.model_dump(mode="json"),
                },
            )
            self._raise_protocol_error(response)
        except httpx.HTTPError as error:
            raise ConnectionError("MetaStore compare-and-set failed") from error

        payload = response.json()
        status = payload.get("status")
        if status == "applied":
            return Applied(snapshot=VersionedLogChain.model_validate(payload["snapshot"]))
        if status == "version_mismatch":
            return VersionMismatch(current=VersionedLogChain.model_validate(payload["current"]))
        raise ValueError("unknown MetaStore HTTP result")
