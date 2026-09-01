import asyncio
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import httpx

from .deployment import NodeProcessState
from .manifest import LabManifest, NodeManifest, new_database_process_ids


def _available_ports(count: int) -> tuple[int, ...]:
    listeners: list[socket.socket] = []
    try:
        for _ in range(count):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listeners.append(listener)
        return tuple(int(listener.getsockname()[1]) for listener in listeners)
    finally:
        for listener in listeners:
            listener.close()


class ManagedProcess(Protocol):
    returncode: int | None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def send_signal(self, signal_number: int) -> None: ...

    async def wait(self) -> int: ...


class ProcessLauncher(Protocol):
    async def launch(self, command: tuple[str, ...], log_path: Path) -> ManagedProcess: ...


class PopenManagedProcess:
    """Async facade over Popen without asyncio's SIGCHLD child watcher."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    @property
    def returncode(self) -> int | None:
        return self._process.poll()

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    def send_signal(self, signal_number: int) -> None:
        self._process.send_signal(signal_number)

    async def wait(self) -> int:
        return await asyncio.to_thread(self._process.wait)


class SubprocessLauncher(ProcessLauncher):
    async def launch(self, command: tuple[str, ...], log_path: Path) -> ManagedProcess:
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def launch() -> PopenManagedProcess:
            log_file = log_path.open("ab")
            try:
                process = subprocess.Popen(
                    command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                log_file.close()
            return PopenManagedProcess(process)

        return cast(ManagedProcess, await asyncio.to_thread(launch))


class SubprocessNodeSupervisor:
    def __init__(
        self,
        manifest: LabManifest,
        *,
        launcher: ProcessLauncher | None = None,
        client: httpx.AsyncClient | None = None,
        meta_nodes: int | None = None,
        db_nodes: int | None = None,
        port_allocator: Callable[[int], tuple[int, ...]] = _available_ports,
    ) -> None:
        self.manifest = manifest
        self._launcher: ProcessLauncher = launcher or SubprocessLauncher()
        self._client = client or httpx.AsyncClient(timeout=0.5)
        self._owns_client = client is None
        self._processes: dict[str, ManagedProcess] = {}
        self._paused: set[str] = set()
        self._lock = asyncio.Lock()
        self._deployment_lock = asyncio.Lock()
        self._initial_meta_nodes = (
            len(manifest.metastore_nodes) if meta_nodes is None else meta_nodes
        )
        self._initial_db_nodes = (
            len(manifest.active_database_nodes) if db_nodes is None else db_nodes
        )
        self._port_allocator = port_allocator

    def _node(self, node_id: str) -> NodeManifest:
        try:
            return self.manifest.nodes[node_id]
        except KeyError as error:
            raise ValueError(f"unknown node: {node_id}") from error

    def _active_node(self, node_id: str) -> NodeManifest:
        node = self._node(node_id)
        if node.process_removed:
            raise ValueError(f"removed process: {node_id}")
        return node

    def command_for(self, node_id: str) -> tuple[str, ...]:
        node = self._active_node(node_id)
        port = node.endpoint.rsplit(":", 1)[1]
        if node.group == "metastore":
            peer_args = tuple(
                value
                for peer in self.manifest.nodes.values()
                if peer.group == "metastore"
                for value in ("--peer", f"{peer.node_id}={peer.endpoint}")
            )
            return (
                sys.executable,
                "-m",
                "delos_lab.metastore.paxos.process",
                "--node-id",
                node_id,
                *peer_args,
                "--db",
                str(node.database),
                "--port",
                port,
            )
        db_args = tuple(
            value
            for peer in self.manifest.nodes.values()
            if peer.group == "database" and not peer.retired
            for value in ("--db-peer", f"{peer.node_id}={peer.endpoint}")
        )
        manifest_args = ("--manifest", str(self.manifest.path))
        meta_args = tuple(
            value
            for peer in self.manifest.nodes.values()
            if peer.group == "metastore"
            for value in ("--meta-peer", f"{peer.node_id}={peer.endpoint}")
        )
        join_args = ("--join-existing-database",) if node.join_existing_database else ()
        return (
            sys.executable,
            "-m",
            "delos_lab.runtime.converged_process",
            "--node-id",
            node_id,
            *db_args,
            *meta_args,
            *manifest_args,
            "--db",
            str(node.database),
            *join_args,
            "--port",
            port,
        )

    async def add_database_node(self, *, timeout: float = 15.0) -> NodeManifest:
        async with self._deployment_lock:
            return await self._add_database_node(timeout=timeout)

    async def _add_database_node(self, *, timeout: float) -> NodeManifest:
        node = self.manifest.pending_database_node
        if node is None:
            listener = socket.socket()
            try:
                listener.bind(("127.0.0.1", 0))
                port = int(listener.getsockname()[1])
            finally:
                listener.close()
            node = self.manifest.add_database_node(f"http://127.0.0.1:{port}")
        await self.start_node(node.node_id)
        await self.wait_healthy((node.node_id,), timeout=timeout)
        response = await self._client.post(
            f"{node.endpoint}/internal/native-loglet/storage-membership/join",
            timeout=timeout,
        )
        response.raise_for_status()
        return self.manifest.mark_database_node_joined(node.node_id)

    def is_running(self, node_id: str) -> bool:
        process = self._processes.get(node_id)
        return process is not None and process.returncode is None

    def is_paused(self, node_id: str) -> bool:
        return self.is_running(node_id) and node_id in self._paused

    async def start_node(self, node_id: str) -> None:
        self._active_node(node_id)
        async with self._lock:
            if self.is_running(node_id):
                return
            self._processes[node_id] = await self._launcher.launch(
                self.command_for(node_id),
                self.manifest.runtime_dir / f"{node_id}.log",
            )
            self._paused.discard(node_id)

    async def resume_node(self, node_id: str) -> None:
        self._active_node(node_id)
        async with self._lock:
            process = self._processes.get(node_id)
            if process is not None and process.returncode is None:
                if node_id in self._paused:
                    process.send_signal(signal.SIGCONT)
                    self._paused.remove(node_id)
                return
        await self.start_node(node_id)

    async def pause_node(self, node_id: str) -> None:
        self._active_node(node_id)
        async with self._lock:
            process = self._processes.get(node_id)
            if process is None or process.returncode is not None or node_id in self._paused:
                return
            process.send_signal(signal.SIGSTOP)
            self._paused.add(node_id)

    async def kill_node(self, node_id: str) -> None:
        node = self._active_node(node_id)
        if node.group != "database":
            raise ValueError("only database processes can be killed")
        async with self._lock:
            process = self._processes.pop(node_id, None)
            self._paused.discard(node_id)
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        self.manifest.retire_database_node(node_id)

    async def stop_node(self, node_id: str) -> None:
        self._node(node_id)
        async with self._lock:
            process = self._processes.pop(node_id, None)
        if process is None or process.returncode is not None:
            return
        if node_id in self._paused:
            process.send_signal(signal.SIGCONT)
            self._paused.discard(node_id)
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def states(
        self,
        node_ids: tuple[str, ...],
    ) -> dict[str, NodeProcessState]:
        states = self.process_states()
        return {
            node_id: NodeProcessState(
                lifecycle=(
                    "removed"
                    if self._node(node_id).process_removed
                    else "paused"
                    if bool(states[node_id]["paused"])
                    else "running"
                    if bool(states[node_id]["running"])
                    else "exited"
                ),
                running=bool(states[node_id]["running"]),
                returncode=states[node_id]["returncode"],  # type: ignore[arg-type]
            )
            for node_id in node_ids
        }

    async def wait_healthy(self, node_ids: tuple[str, ...], *, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        pending = set(node_ids)
        while pending and time.monotonic() < deadline:
            for node_id in tuple(pending):
                try:
                    response = await self._client.get(f"{self._node(node_id).endpoint}/health")
                    if response.status_code == 200 and response.json().get("status") == "ok":
                        pending.remove(node_id)
                except httpx.HTTPError:
                    pass
            if pending:
                await asyncio.sleep(0.05)
        if pending:
            raise TimeoutError(f"nodes did not become healthy: {sorted(pending)}")

    async def start_cluster(self, *, timeout: float = 10.0) -> None:
        metas = tuple(
            node.node_id
            for node in self.manifest.nodes.values()
            if node.group == "metastore" and not node.process_removed
        )
        databases = tuple(
            node.node_id
            for node in self.manifest.active_database_nodes
            if not node.join_existing_database
        )
        pending = tuple(
            node.node_id
            for node in self.manifest.active_database_nodes
            if node.join_existing_database
        )
        await asyncio.gather(*(self.start_node(node_id) for node_id in metas))
        await self.wait_healthy(metas, timeout=timeout)
        await asyncio.gather(*(self.start_node(node_id) for node_id in databases))
        await self.wait_healthy(databases, timeout=timeout)
        if pending:
            await self.add_database_node(timeout=timeout)

    async def stop_cluster(self) -> None:
        await asyncio.gather(*(self.stop_node(node_id) for node_id in tuple(self.manifest.nodes)))

    def _managed_artifacts(self) -> tuple[Path, ...]:
        artifacts = {self.manifest.path, self.manifest.path.with_suffix(".json.tmp")}
        for node in self.manifest.nodes.values():
            artifacts.update(
                {
                    node.database,
                    Path(f"{node.database}-shm"),
                    Path(f"{node.database}-wal"),
                    self.manifest.runtime_dir / f"{node.node_id}.log",
                }
            )
        return tuple(artifacts)

    def _delete_managed_artifacts(self, artifacts: tuple[Path, ...]) -> None:
        runtime_dir = self.manifest.runtime_dir.resolve()
        for artifact in artifacts:
            resolved = artifact.resolve()
            if resolved == runtime_dir or not resolved.is_relative_to(runtime_dir):
                raise ValueError(f"refusing to reset unmanaged path: {artifact}")
        for artifact in artifacts:
            artifact.unlink(missing_ok=True)

    async def reset_cluster(self, *, timeout: float = 10.0) -> None:
        """Discard this lab runtime and bootstrap a fresh cluster of the configured size."""
        async with self._deployment_lock:
            ports = self._port_allocator(self._initial_meta_nodes + self._initial_db_nodes)
            database_ids = new_database_process_ids(
                self._initial_db_nodes,
                existing=set(self.manifest.nodes),
            )
            replacement = LabManifest.create_subprocess(
                self.manifest.runtime_dir,
                meta_ports=ports[: self._initial_meta_nodes],
                db_ports=ports[self._initial_meta_nodes :],
                database_ids=database_ids,
            )
            artifacts = self._managed_artifacts()
            await self.stop_cluster()
            self._delete_managed_artifacts(artifacts)
            self._processes.clear()
            self._paused.clear()
            self.manifest.nodes = replacement.nodes
            self.manifest.save()
            await self.start_cluster(timeout=timeout)

    def process_states(self) -> dict[str, dict[str, object]]:
        return {
            node_id: {
                "running": self.is_running(node_id),
                "paused": self.is_paused(node_id),
                "returncode": None if process is None else process.returncode,
            }
            for node_id in self.manifest.nodes
            for process in (self._processes.get(node_id),)
        }

    async def close(self) -> None:
        await self.stop_cluster()
        if self._owns_client:
            await self._client.aclose()
