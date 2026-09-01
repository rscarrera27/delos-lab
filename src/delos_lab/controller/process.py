import argparse
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from delos_lab.common.membership import quorum_size

from .http_api import LabController, create_controller_app
from .manifest import LabManifest
from .process_manager import SubprocessNodeSupervisor, _available_ports


def _node_count(value: str) -> int:
    count = int(value)
    try:
        quorum_size(count)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return count


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Delos Lab local process supervisor")
    parser.add_argument("--runtime-dir", type=Path, default=Path(".runtime"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9400)
    parser.add_argument("--no-auto-start", action="store_true")
    parser.add_argument("--meta-nodes", type=_node_count, default=3)
    parser.add_argument("--db-nodes", type=_node_count, default=3)
    parser.add_argument("--static-dir", type=Path)
    return parser


def _mount_spa(api: FastAPI, static_dir: Path | None) -> None:
    web_root = (
        static_dir
        if static_dir is not None
        else Path(__file__).resolve().parents[3] / "frontend" / "dist"
    )
    if (web_root / "index.html").is_file():
        api.mount("/", StaticFiles(directory=web_root, html=True), name="spa")


def build_controller_app(
    runtime_dir: Path,
    *,
    auto_start: bool,
    meta_nodes: int = 3,
    db_nodes: int = 3,
    static_dir: Path | None = None,
) -> FastAPI:
    runtime_dir = runtime_dir.resolve()
    manifest_path = runtime_dir / "manifest.json"
    if manifest_path.exists():
        manifest = LabManifest.load(manifest_path)
        if len(manifest.metastore_nodes) != meta_nodes:
            raise ValueError("saved MetaStore node count does not match configuration")
        if len(manifest.database_nodes) < db_nodes:
            raise ValueError("saved database node count is smaller than configuration")
    else:
        ports = _available_ports(meta_nodes + db_nodes)
        manifest = LabManifest.create_subprocess(
            runtime_dir,
            meta_ports=ports[:meta_nodes],
            db_ports=ports[meta_nodes:],
        )
        manifest.save()
    observation_client = httpx.AsyncClient(timeout=1.0)
    proxy_client = httpx.AsyncClient(timeout=10.0)
    supervisor = SubprocessNodeSupervisor(
        manifest,
        client=observation_client,
        meta_nodes=meta_nodes,
        db_nodes=db_nodes,
    )
    controller = LabController(
        manifest,
        supervisor,
        observation_client,
        proxy_client=proxy_client,
    )
    api = create_controller_app(controller)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            if auto_start:
                await controller.start_cluster()
            yield
        finally:
            await supervisor.close()
            await proxy_client.aclose()
            await observation_client.aclose()

    api.router.lifespan_context = lifespan
    _mount_spa(api, static_dir)
    return api


async def serve_controller(
    *,
    runtime_dir: Path,
    host: str,
    port: int,
    auto_start: bool,
    meta_nodes: int = 3,
    db_nodes: int = 3,
    static_dir: Path | None = None,
) -> None:
    app = build_controller_app(
        runtime_dir,
        auto_start=auto_start,
        meta_nodes=meta_nodes,
        db_nodes=db_nodes,
        static_dir=static_dir,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    await server.serve()


def main() -> None:
    arguments = _parser().parse_args()
    try:
        asyncio.run(
            serve_controller(
                runtime_dir=arguments.runtime_dir,
                host=arguments.host,
                port=arguments.port,
                auto_start=not arguments.no_auto_start,
                meta_nodes=arguments.meta_nodes,
                db_nodes=arguments.db_nodes,
                static_dir=arguments.static_dir,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
