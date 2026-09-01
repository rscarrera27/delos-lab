from pathlib import Path

import httpx

from delos_lab.controller.deployment import NodeProcessState, NodeRecord
from delos_lab.controller.topology import DeploymentCollector


class Directory:
    def __init__(self, tmp_path: Path) -> None:
        self.configured = {"meta": 1, "db": 1}
        self._nodes = (
            NodeRecord(
                node_id="meta-1",
                service="meta",
                endpoint="http://meta:9200",
                database=tmp_path / "meta.sqlite3",
            ),
            NodeRecord(
                node_id="db-1",
                service="db",
                endpoint="http://db:9300",
                database=tmp_path / "db.sqlite3",
            ),
        )

    def nodes(self, service: str | None = None) -> tuple[NodeRecord, ...]:
        return tuple(node for node in self._nodes if service is None or node.service == service)


class Lifecycle:
    async def states(self, node_ids: tuple[str, ...]) -> dict[str, NodeProcessState]:
        return {
            node_id: NodeProcessState(
                lifecycle="running",
                running=True,
                returncode=None,
            )
            for node_id in node_ids
        }


class PausedLifecycle:
    async def states(self, node_ids: tuple[str, ...]) -> dict[str, NodeProcessState]:
        return {
            node_id: NodeProcessState(lifecycle="paused", running=True, returncode=None)
            for node_id in node_ids
        }


async def test_deployment_separates_process_liveness_from_controller_reachability(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "meta":
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(
                200,
                json={
                    "node_id": "meta-1",
                    "state": {
                        "last_applied": 0,
                        "state_machine": {"version": 0, "chain": None},
                    },
                },
            )
        raise httpx.ConnectError("not reachable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        snapshot = await DeploymentCollector(Directory(tmp_path), Lifecycle(), client).collect()

    assert snapshot.services["meta"].model_dump() == {
        "name": "meta",
        "configured": 1,
        "running": 1,
        "reachable": 1,
    }
    assert snapshot.services["db"].model_dump() == {
        "name": "db",
        "configured": 1,
        "running": 1,
        "reachable": 0,
    }
    assert snapshot.nodes["meta-1"].metastore is not None
    assert snapshot.nodes["meta-1"].metastore.state.last_applied == 0
    assert snapshot.nodes["db-1"].reachable is False
    assert snapshot.nodes["db-1"].error == "not reachable"


async def test_deployment_does_not_probe_a_controller_paused_process(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("paused process must not be probed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        snapshot = await DeploymentCollector(
            Directory(tmp_path), PausedLifecycle(), client
        ).collect()

    assert requests == []
    assert snapshot.services["meta"].running == 1
    assert snapshot.services["meta"].reachable == 0
    assert snapshot.nodes["meta-1"].lifecycle == "paused"
