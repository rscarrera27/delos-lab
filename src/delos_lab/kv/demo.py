import asyncio
import socket
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from delos_lab.common.events import EventValue, LabEvent
from delos_lab.metastore.paxos.http_transport import HttpMetaStorePeer
from delos_lab.native_loglet.config import NativeLogletConfiguration

_DB_MEMBERS = ("db-1", "db-2", "db-3")
_META_MEMBERS = ("meta-1", "meta-2", "meta-3")


def _event(kind: str, details: dict[str, EventValue]) -> LabEvent:
    return LabEvent(
        timestamp=time.monotonic(),
        component="paxos-backed-kv-database",
        kind=kind,
        details=details,
    )


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


async def _wait_until(
    operation: Callable[[], Awaitable[object | None]],
    *,
    timeout: float,
    description: str,
) -> object:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            result = await operation()
            if result is not None:
                return result
        except (ConnectionError, httpx.HTTPError, TypeError, ValueError) as error:
            last_error = error
        await asyncio.sleep(0.05)
    suffix = "" if last_error is None else f": {last_error}"
    raise TimeoutError(f"timed out waiting for {description}{suffix}")


class _ProcessCluster:
    def __init__(
        self,
        runtime_dir: Path,
        db_urls: dict[str, str],
        meta_urls: dict[str, str],
    ) -> None:
        self.runtime_dir = runtime_dir
        self.db_urls = db_urls
        self.meta_urls = meta_urls
        self.processes: dict[str, asyncio.subprocess.Process] = {}

    async def start_meta(self, node_id: str) -> None:
        peer_args = [
            value for item in self.meta_urls.items() for value in ("--peer", "=".join(item))
        ]
        await self._start(
            node_id,
            "delos_lab.metastore.paxos.process",
            "--node-id",
            node_id,
            *peer_args,
            "--db",
            str(self.runtime_dir / f"{node_id}.sqlite3"),
            "--port",
            self.meta_urls[node_id].rsplit(":", 1)[1],
        )

    async def start_db(self, node_id: str) -> None:
        db_args = [
            value for item in self.db_urls.items() for value in ("--db-peer", "=".join(item))
        ]
        meta_args = [
            value for item in self.meta_urls.items() for value in ("--meta-peer", "=".join(item))
        ]
        await self._start(
            node_id,
            "delos_lab.runtime.converged_process",
            "--node-id",
            node_id,
            *db_args,
            *meta_args,
            "--db",
            str(self.runtime_dir / f"{node_id}.sqlite3"),
            "--port",
            self.db_urls[node_id].rsplit(":", 1)[1],
        )

    async def _start(self, node_id: str, module: str, *arguments: str) -> None:
        self.processes[node_id] = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            module,
            *arguments,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

    async def stop(self, node_id: str) -> None:
        process = self.processes.pop(node_id)
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def close(self) -> None:
        await asyncio.gather(*(self.stop(node_id) for node_id in tuple(self.processes)))


async def _run_demo(runtime_dir: Path, timeout: float) -> list[LabEvent]:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    ports = _available_ports(6)
    meta_urls = {
        member: f"http://127.0.0.1:{port}"
        for member, port in zip(_META_MEMBERS, ports[:3], strict=True)
    }
    db_urls = {
        member: f"http://127.0.0.1:{port}"
        for member, port in zip(_DB_MEMBERS, ports[3:], strict=True)
    }
    cluster = _ProcessCluster(runtime_dir, db_urls, meta_urls)
    events: list[LabEvent] = []

    async with httpx.AsyncClient(timeout=1.5) as client:

        async def healthy(url: str) -> bool | None:
            response = await client.get(f"{url}/health")
            response.raise_for_status()
            return True if response.json().get("status") == "ok" else None

        async def all_healthy(urls: dict[str, str]) -> bool | None:
            for url in urls.values():
                try:
                    if await healthy(url) is None:
                        return None
                except httpx.HTTPError:
                    return None
            return True

        async def successful(
            method: str, url: str, json: object | None = None
        ) -> httpx.Response | None:
            response = await client.request(method, url, json=json)
            if response.status_code >= 500:
                return None
            response.raise_for_status()
            return response

        try:
            await asyncio.gather(*(cluster.start_meta(node) for node in _META_MEMBERS))
            await _wait_until(
                lambda: all_healthy(meta_urls),
                timeout=timeout,
                description="three healthy Paxos peers",
            )
            await asyncio.gather(*(cluster.start_db(node) for node in _DB_MEMBERS))
            await _wait_until(
                lambda: all_healthy(db_urls),
                timeout=timeout,
                description="three healthy DB peers",
            )
            events.append(_event("cluster_started", {"db_peers": 3, "meta_peers": 3}))

            put = await _wait_until(
                lambda: successful(
                    "PUT",
                    f"{db_urls['db-3']}/kv/count",
                    {"client_id": "demo", "request_id": "put-1", "value": 1},
                ),
                timeout=timeout,
                description="initial KV put",
            )
            if not isinstance(put, httpx.Response):
                raise TypeError("PUT did not return an HTTP response")
            snapshot = await HttpMetaStorePeer(meta_urls["meta-1"], client).read()
            if snapshot.chain is None:
                raise TypeError("initial LogChain was not installed")
            events.append(_event("chain_bootstrapped", {"version": snapshot.version}))
            events.append(_event("put_applied", {"value": int(put.json()["value"])}))

            read = await _wait_until(
                lambda: successful("GET", f"{db_urls['db-2']}/kv/count"),
                timeout=timeout,
                description="cross-database-replica read",
            )
            if not isinstance(read, httpx.Response):
                raise TypeError("GET did not return an HTTP response")
            events.append(_event("database_replicas_agreed", {"value": int(read.json()["value"])}))

            await cluster.stop("db-1")
            events.append(_event("sequencer_stopped", {"node_id": "db-1"}))

            increment = await _wait_until(
                lambda: successful(
                    "POST",
                    f"{db_urls['db-3']}/kv/count/increment",
                    {"client_id": "demo", "request_id": "inc-1", "delta": 1},
                ),
                timeout=timeout,
                description="increment after sequencer failure",
            )
            if not isinstance(increment, httpx.Response):
                raise TypeError("increment did not return an HTTP response")
            reconfigured = await HttpMetaStorePeer(meta_urls["meta-2"], client).read()
            if reconfigured.chain is None:
                raise TypeError("reconfigured LogChain is absent")
            events.append(
                _event(
                    "chain_reconfigured",
                    {
                        "version": reconfigured.version,
                        "sequencer": NativeLogletConfiguration.from_generic(
                            reconfigured.chain.active.loglet
                        ).sequencer_node,
                    },
                )
            )
            events.append(_event("increment_applied", {"value": int(increment.json()["value"])}))

            await cluster.start_db("db-1")
            await _wait_until(
                lambda: healthy(db_urls["db-1"]),
                timeout=timeout,
                description="restarted db-1",
            )
            events.append(_event("peer_restarted", {"node_id": "db-1"}))
            caught_up = await _wait_until(
                lambda: successful("GET", f"{db_urls['db-1']}/kv/count"),
                timeout=timeout,
                description="restarted DB catch-up",
            )
            if not isinstance(caught_up, httpx.Response):
                raise TypeError("restarted GET did not return an HTTP response")
            events.append(
                _event(
                    "peer_caught_up",
                    {"node_id": "db-1", "value": int(caught_up.json()["value"])},
                )
            )
            return events
        finally:
            await cluster.close()


async def run_demo(
    runtime_dir: Path | None = None,
    *,
    timeout: float = 15.0,
) -> list[LabEvent]:
    if runtime_dir is not None:
        return await _run_demo(runtime_dir, timeout)
    with tempfile.TemporaryDirectory(prefix="delos-kv-") as temporary:
        return await _run_demo(Path(temporary), timeout)


def main() -> None:
    for event in asyncio.run(run_demo()):
        print(event.model_dump_json())


if __name__ == "__main__":
    main()
