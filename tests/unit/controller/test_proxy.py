from pathlib import Path

import httpx
import pytest

from delos_lab.controller.deployment import NodeRecord
from delos_lab.controller.proxy import (
    NodeProxy,
    NodeUnavailable,
    UnsupportedNodeService,
)

DB_ID = "d" * 64
META_ID = "e" * 64


class NodeProxyDirectory:
    def __init__(self) -> None:
        self.records = {
            DB_ID: NodeRecord(
                node_id=DB_ID,
                service="db",
                endpoint="http://db-control:9300",
                database=Path("/var/lib/delos/node.sqlite3"),
            ),
            META_ID: NodeRecord(
                node_id=META_ID,
                service="meta",
                endpoint="http://meta-control:9200",
                database=Path("/var/lib/delos/node.sqlite3"),
            ),
        }

    def require(self, node_id: str) -> NodeRecord:
        try:
            return self.records[node_id]
        except KeyError as error:
            raise ValueError(f"unknown node: {node_id}") from error


async def test_proxy_preserves_method_body_query_and_upstream_response() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            409,
            json={"code": "CAS_MISMATCH", "detail": "different value"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await NodeProxy(NodeProxyDirectory(), client).forward(
            DB_ID,
            method="POST",
            path="/kv/count/compare-and-set",
            query="trace=1",
            body=b'{"expected":1,"value":2}',
            content_type="application/json",
        )

    assert len(observed) == 1
    assert observed[0].url == "http://db-control:9300/kv/count/compare-and-set?trace=1"
    assert observed[0].method == "POST"
    assert observed[0].content == b'{"expected":1,"value":2}'
    assert response.status_code == 409
    assert response.body == b'{"code":"CAS_MISMATCH","detail":"different value"}'
    assert response.content_type == "application/json"


async def test_proxy_rejects_non_database_node() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    ) as client:
        with pytest.raises(UnsupportedNodeService):
            await NodeProxy(NodeProxyDirectory(), client).forward(
                META_ID,
                method="GET",
                path="/kv/key",
            )


async def test_proxy_maps_transport_failure_to_node_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NodeUnavailable):
            await NodeProxy(NodeProxyDirectory(), client).forward(
                DB_ID,
                method="GET",
                path="/kv/key",
            )
