import asyncio

import httpx
import pytest

from delos_lab.native_loglet.errors import PositionTrimmed, PredecessorUnavailable, SegmentSealed
from delos_lab.native_loglet.http_api import create_loglet_app
from delos_lab.native_loglet.http_transport import HttpLogletTransport
from delos_lab.native_loglet.memory_store import MemoryLogletStore
from delos_lab.native_loglet.types import LogEntry


async def test_http_api_writes_reads_and_seals_segment() -> None:
    store = MemoryLogletStore("db-1")
    app = create_loglet_app(store)
    entry = LogEntry(segment_id="s", position=0, command_id="r1", payload=b"a")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://db-1"
    ) as client:
        response = await client.put(
            "/segments/s/entries/0",
            json={"entry": entry.model_dump(mode="json"), "known_tail": 0},
        )
        assert response.status_code == 200
        assert response.json() == {
            "segment_id": "s",
            "local_tail": 1,
            "trimmed_prefix": 0,
            "known_tail": 0,
            "sealed": False,
        }

        response = await client.get("/segments/s/state", params={"known_tail": 1})
        assert response.json() == {
            "segment_id": "s",
            "local_tail": 1,
            "trimmed_prefix": 0,
            "known_tail": 1,
            "sealed": False,
        }

        response = await client.post("/segments/s/seal", json={"known_tail": 1})
        assert response.status_code == 200
        assert response.json()["sealed"] is True

        response = await client.get("/segments/s/entries", params={"start": 0, "limit": 10})
        assert response.json() == [entry.model_dump(mode="json")]

        assert (await client.get("/segments/s/entries", params={"start": -1})).status_code == 422
        assert (await client.get("/segments/s/entries", params={"limit": 201})).status_code == 422


async def test_http_api_restores_predecessor_error_and_allows_sealed_repair() -> None:
    store = MemoryLogletStore("db-1")
    app = create_loglet_app(store)
    second = LogEntry(segment_id="s", position=1, command_id="r2", payload=b"b")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://db-1"
    ) as client:
        response = await client.put(
            "/segments/s/entries/1",
            json={"entry": second.model_dump(mode="json"), "known_tail": 0},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "PREDECESSOR_UNAVAILABLE"

        await client.post("/segments/s/seal", json={"known_tail": 0})
        response = await client.put(
            "/segments/s/repairs/1",
            json={"entry": second.model_dump(mode="json"), "known_tail": 0},
        )
        assert response.status_code == 200
        assert response.json()["local_tail"] == 2


async def test_http_transport_round_trips_protocol_and_restores_errors() -> None:
    store = MemoryLogletStore("db-1")
    app = create_loglet_app(store)
    entry = LogEntry(segment_id="s", position=0, command_id="r1", payload=b"a")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http_client:
        transport = HttpLogletTransport({"db-1": "http://db-1"}, http_client)
        observed = await transport.put("db-1", entry, known_tail=0)
        assert observed.local_tail == 1
        assert await transport.get("db-1", "s", 0, known_tail=1) == entry
        assert (await transport.state("db-1", "s", known_tail=1)).known_tail == 1
        assert (await transport.prefix_trim("db-1", "s", 1)).trimmed_prefix == 1
        assert await transport.get("db-1", "s", 0, known_tail=1) is None
        with pytest.raises(PositionTrimmed):
            await transport.repair("db-1", entry, known_tail=1)
        assert (await transport.seal("db-1", "s", known_tail=1)).sealed is True

        with pytest.raises(SegmentSealed):
            await transport.put(
                "db-1",
                LogEntry(segment_id="s", position=1, command_id="r2", payload=b"b"),
                known_tail=1,
            )

        with pytest.raises(PredecessorUnavailable):
            await HttpLogletTransport(
                {"db-2": "http://db-2"},
                http_client,
            ).put(
                "db-2",
                LogEntry(segment_id="other", position=1, command_id="r3", payload=b"c"),
                known_tail=0,
            )


async def test_http_tail_notification_waits_for_local_progress() -> None:
    store = MemoryLogletStore("db-1")
    app = create_loglet_app(store)
    entry = LogEntry(segment_id="s", position=0, command_id="r1", payload=b"a")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http_client:
        transport = HttpLogletTransport({"db-1": "http://db-1"}, http_client)
        waiting = asyncio.create_task(transport.wait_for_tail("db-1", "s", 1))
        await asyncio.sleep(0)

        assert not waiting.done()

        await transport.put("db-1", entry)
        state = await asyncio.wait_for(waiting, timeout=1)
        assert (state.local_tail, state.sealed) == (1, False)
