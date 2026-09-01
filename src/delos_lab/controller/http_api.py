from typing import Protocol
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .deployment import DeploymentSnapshot, NodeProcessState, StaticNodeDirectory
from .manifest import LabManifest, NodeManifest
from .proxy import NodeProxy, NodeProxyError
from .topology import DeploymentCollector


class NodeSupervisor(Protocol):
    async def start_cluster(self, *, timeout: float = 10.0) -> None: ...

    async def stop_cluster(self) -> None: ...

    async def reset_cluster(self, *, timeout: float = 10.0) -> None: ...

    async def resume_node(self, node_id: str) -> None: ...

    async def pause_node(self, node_id: str) -> None: ...

    async def kill_node(self, node_id: str) -> None: ...

    async def add_database_node(self, *, timeout: float = 15.0) -> NodeManifest: ...

    async def states(
        self,
        node_ids: tuple[str, ...],
    ) -> dict[str, NodeProcessState]: ...


class DeploymentReader(Protocol):
    async def collect(self) -> DeploymentSnapshot: ...


class UnsupportedProcessAction(Exception):
    pass


class LabController:
    def __init__(
        self,
        manifest: LabManifest,
        supervisor: NodeSupervisor,
        node_client: httpx.AsyncClient,
        *,
        deployment: DeploymentReader | None = None,
        proxy_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.manifest = manifest
        self.directory = StaticNodeDirectory(manifest)
        self.supervisor = supervisor
        self.deployment = deployment or DeploymentCollector(
            self.directory,
            supervisor,
            node_client,
        )
        self.proxy = NodeProxy(self.directory, proxy_client or node_client)

    async def start_cluster(self) -> None:
        await self.supervisor.start_cluster()

    async def stop_cluster(self) -> None:
        await self.supervisor.stop_cluster()

    async def reset_cluster(self) -> None:
        await self.supervisor.reset_cluster()

    async def resume_node(self, node_id: str) -> None:
        self.directory.require(node_id)
        await self.supervisor.resume_node(node_id)

    async def pause_node(self, node_id: str) -> None:
        self.directory.require(node_id)
        await self.supervisor.pause_node(node_id)

    async def kill_node(self, node_id: str) -> None:
        node = self.directory.require(node_id)
        if node.service != "db":
            raise UnsupportedProcessAction("MetaStore processes support only resume and pause")
        await self.supervisor.kill_node(node_id)

    async def add_database_node(self) -> NodeManifest:
        return await self.supervisor.add_database_node()


def create_controller_app(controller: LabController) -> FastAPI:
    app = FastAPI(title="Delos Lab Controller")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/manifest")
    async def manifest() -> dict[str, object]:
        return controller.manifest.model_dump(mode="json")

    @app.get("/api/deployment")
    async def deployment() -> dict[str, object]:
        return (await controller.deployment.collect()).model_dump(mode="json")

    @app.post("/api/cluster/start", status_code=204)
    async def start_cluster() -> Response:
        await controller.start_cluster()
        return Response(status_code=204)

    @app.post("/api/cluster/stop", status_code=204)
    async def stop_cluster() -> Response:
        await controller.stop_cluster()
        return Response(status_code=204)

    @app.post("/api/cluster/reset", status_code=204)
    async def reset_cluster() -> Response:
        try:
            await controller.reset_cluster()
        except TimeoutError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return Response(status_code=204)

    @app.post("/api/database-nodes", status_code=201)
    async def add_database_node() -> dict[str, object]:
        try:
            node = await controller.add_database_node()
        except (ConnectionError, TimeoutError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return node.model_dump(mode="json")

    async def node_action(node_id: str, action: str) -> Response:
        try:
            operation = getattr(controller, f"{action}_node")
            await operation(node_id)
        except UnsupportedProcessAction as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return Response(status_code=204)

    @app.post("/api/nodes/{node_id}/resume", status_code=204)
    async def resume_node(node_id: str) -> Response:
        return await node_action(node_id, "resume")

    @app.post("/api/nodes/{node_id}/pause", status_code=204)
    async def pause_node(node_id: str) -> Response:
        return await node_action(node_id, "pause")

    @app.post("/api/nodes/{node_id}/kill", status_code=204)
    async def kill_node(node_id: str) -> Response:
        return await node_action(node_id, "kill")

    async def proxy_request(
        node_id: str,
        request: Request,
        target_path: str,
    ) -> Response:
        try:
            upstream = await controller.proxy.forward(
                node_id,
                method=request.method,
                path=target_path,
                query=request.url.query,
                body=await request.body(),
                content_type=request.headers.get("content-type"),
            )
        except ValueError as error:
            return JSONResponse(
                status_code=404,
                content={"code": "UNKNOWN_NODE", "detail": str(error)},
            )
        except NodeProxyError as error:
            return JSONResponse(
                status_code=error.status_code,
                content={"code": error.code, "detail": str(error)},
            )
        headers = (
            {"content-type": upstream.content_type} if upstream.content_type is not None else None
        )
        return Response(
            content=upstream.body,
            status_code=upstream.status_code,
            headers=headers,
        )

    @app.api_route("/api/nodes/{node_id}/kv/{key}", methods=["GET", "PUT", "DELETE"])
    async def proxy_kv(node_id: str, key: str, request: Request) -> Response:
        return await proxy_request(node_id, request, f"/kv/{quote(key, safe='')}")

    @app.post("/api/nodes/{node_id}/kv/{key}/compare-and-set")
    async def proxy_compare_and_set(node_id: str, key: str, request: Request) -> Response:
        return await proxy_request(
            node_id,
            request,
            f"/kv/{quote(key, safe='')}/compare-and-set",
        )

    @app.post("/api/nodes/{node_id}/kv/{key}/increment")
    async def proxy_increment(node_id: str, key: str, request: Request) -> Response:
        return await proxy_request(
            node_id,
            request,
            f"/kv/{quote(key, safe='')}/increment",
        )

    @app.get("/api/nodes/{node_id}/segments/{segment_id}/entries")
    async def proxy_log_entries(
        node_id: str,
        segment_id: str,
        request: Request,
    ) -> Response:
        return await proxy_request(
            node_id,
            request,
            f"/segments/{quote(segment_id, safe='')}/entries",
        )

    return app
