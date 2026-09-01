from collections.abc import Sequence

import httpx
from pydantic import ValidationError

from delos_lab.kv.snapshot import KvSnapshot


class HttpDatabaseSnapshotSource:
    """Fetch a validated application snapshot from any existing database replica."""

    def __init__(self, endpoints: Sequence[str], client: httpx.AsyncClient) -> None:
        self._endpoints = tuple(endpoint.rstrip("/") for endpoint in endpoints)
        self._client = client

    async def fetch(self) -> KvSnapshot:
        failures: list[str] = []
        for endpoint in self._endpoints:
            try:
                response = await self._client.get(
                    f"{endpoint}/internal/database/snapshot",
                    timeout=10.0,
                )
                response.raise_for_status()
                return KvSnapshot.model_validate(response.json())
            except (httpx.HTTPError, ValidationError, ValueError) as error:
                failures.append(f"{endpoint}: {error}")
        detail = "; ".join(failures) if failures else "no source replicas were configured"
        raise ConnectionError(f"database bootstrap snapshot is unavailable: {detail}")
