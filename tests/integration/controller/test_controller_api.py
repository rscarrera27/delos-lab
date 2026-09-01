from pathlib import Path

import httpx

from delos_lab.controller.deployment import (
    DeploymentSnapshot,
    NodeObservation,
    NodeProcessState,
    ServiceObservation,
)
from delos_lab.controller.http_api import LabController, create_controller_app
from delos_lab.controller.manifest import LabManifest, NodeManifest


class Supervisor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def start_cluster(self, *, timeout: float = 10.0) -> None:
        del timeout
        self.calls.append(("start_cluster", None))

    async def stop_cluster(self) -> None:
        self.calls.append(("stop_cluster", None))

    async def reset_cluster(self, *, timeout: float = 10.0) -> None:
        del timeout
        self.calls.append(("reset_cluster", None))

    async def resume_node(self, node_id: str) -> None:
        self.calls.append(("resume", node_id))

    async def pause_node(self, node_id: str) -> None:
        self.calls.append(("pause", node_id))

    async def kill_node(self, node_id: str) -> None:
        self.calls.append(("kill", node_id))

    async def add_database_node(self, *, timeout: float = 15.0) -> NodeManifest:
        del timeout
        self.calls.append(("add_database_node", "db-4"))
        return NodeManifest(
            node_id="db-4",
            group="database",
            endpoint="http://127.0.0.1:7",
            database=Path("db-4.sqlite3"),
        )

    async def states(self, node_ids: tuple[str, ...]) -> dict[str, NodeProcessState]:
        return {
            node_id: NodeProcessState(lifecycle="exited", running=False) for node_id in node_ids
        }


class Deployment:
    async def collect(self) -> DeploymentSnapshot:
        return DeploymentSnapshot(
            services={
                "meta": ServiceObservation(name="meta", configured=3, running=3, reachable=3),
                "db": ServiceObservation(name="db", configured=3, running=3, reachable=3),
            },
            nodes={
                "db-1": NodeObservation(
                    node_id="db-1",
                    service="db",
                    lifecycle="running",
                    reachable=False,
                    observed_at=1.0,
                )
            },
            collected_at=1.0,
        )


async def test_controller_exposes_only_supervision_and_browser_proxy_contracts(
    tmp_path: Path,
) -> None:
    manifest = LabManifest.create(
        tmp_path,
        ports=(1, 2, 3, 4, 5, 6),
        database_ids=("db-1", "db-2", "db-3"),
    )
    supervisor = Supervisor()
    upstream_requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, json={"key": "count", "value": 2})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as node_client:
        controller = LabController(
            manifest,
            supervisor,
            node_client,
            deployment=Deployment(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_controller_app(controller)),
            base_url="http://controller",
        ) as client:
            assert (await client.get("/api/health")).json() == {"status": "ok"}
            assert (await client.get("/api/manifest")).json()["nodes"]["db-1"][
                "endpoint"
            ] == "http://127.0.0.1:4"
            deployment = (await client.get("/api/deployment")).json()
            assert deployment["services"]["db"]["configured"] == 3
            assert (await client.post("/api/cluster/start")).status_code == 204
            assert (await client.post("/api/cluster/reset")).status_code == 204
            assert (await client.post("/api/nodes/db-1/pause")).status_code == 204
            assert (await client.post("/api/nodes/db-1/resume")).status_code == 204
            assert (await client.post("/api/nodes/db-1/kill")).status_code == 204
            added = await client.post("/api/database-nodes")
            response = await client.post(
                "/api/nodes/db-1/kv/count/increment",
                json={"delta": 2},
            )
            missing = await client.post("/api/nodes/missing/kill")
            unsupported = await client.post("/api/nodes/meta-1/kill")
            meta_proxy = await client.get("/api/nodes/meta-1/kv/count")

    assert supervisor.calls == [
        ("start_cluster", None),
        ("reset_cluster", None),
        ("pause", "db-1"),
        ("resume", "db-1"),
        ("kill", "db-1"),
        ("add_database_node", "db-4"),
    ]
    assert added.status_code == 201
    assert added.json()["node_id"] == "db-4"
    assert response.status_code == 200
    assert response.json() == {"key": "count", "value": 2}
    assert upstream_requests[0].url == "http://127.0.0.1:4/kv/count/increment"
    assert missing.status_code == 404
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"] == "MetaStore processes support only resume and pause"
    assert meta_proxy.status_code == 422
