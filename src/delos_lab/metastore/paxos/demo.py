import asyncio
import socket
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from delos_lab.common.events import EventValue, LabEvent
from delos_lab.virtual_log.metastore import Applied
from delos_lab.virtual_log.types import LogChain, LogletConfiguration, LogSegment

from .client import PaxosMetaStoreClient
from .errors import PaxosNoQuorum
from .http_transport import HttpMetaStorePeer

_MEMBERS = ("meta-1", "meta-2", "meta-3")


def _event(kind: str, details: dict[str, EventValue]) -> LabEvent:
    return LabEvent(
        timestamp=time.monotonic(),
        component="paxos-metastore",
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


def _chain(segment_id: str) -> LogChain:
    return LogChain(
        segments=(
            LogSegment(
                segment_id=segment_id,
                virtual_start=0,
                virtual_stop=None,
                loglet=LogletConfiguration(
                    kind="demo",
                    parameters={"name": segment_id},
                ),
            ),
        )
    )


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
        except (
            ConnectionError,
            httpx.HTTPError,
            PaxosNoQuorum,
            TypeError,
            ValueError,
        ) as error:
            last_error = error
        await asyncio.sleep(0.05)
    suffix = "" if last_error is None else f": {last_error}"
    raise TimeoutError(f"timed out waiting for {description}{suffix}")


class _ProcessCluster:
    def __init__(self, runtime_dir: Path, urls: dict[str, str]) -> None:
        self.runtime_dir = runtime_dir
        self.urls = urls
        self.processes: dict[str, asyncio.subprocess.Process] = {}

    async def start(self, node_id: str) -> None:
        peer_args = [value for item in self.urls.items() for value in ("--peer", "=".join(item))]
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "delos_lab.metastore.paxos.process",
            "--node-id",
            node_id,
            *peer_args,
            "--db",
            str(self.runtime_dir / f"{node_id}.sqlite3"),
            "--port",
            self.urls[node_id].rsplit(":", 1)[1],
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self.processes[node_id] = process

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
    urls = {
        member: f"http://127.0.0.1:{port}"
        for member, port in zip(
            _MEMBERS,
            _available_ports(len(_MEMBERS)),
            strict=True,
        )
    }
    cluster = _ProcessCluster(runtime_dir, urls)
    events: list[LabEvent] = []

    async with httpx.AsyncClient(timeout=0.75) as client:

        async def health(node_id: str) -> bool | None:
            response = await client.get(f"{urls[node_id]}/health")
            response.raise_for_status()
            return True if response.json().get("status") == "ok" else None

        async def all_healthy() -> bool | None:
            for member in _MEMBERS:
                try:
                    if await health(member) is None:
                        return None
                except httpx.HTTPError:
                    return None
            return True

        try:
            await asyncio.gather(*(cluster.start(member) for member in _MEMBERS))
            await _wait_until(
                all_healthy,
                timeout=timeout,
                description="three healthy Paxos peers",
            )
            events.append(_event("cluster_started", {"peer_count": 3}))

            metastore = PaxosMetaStoreClient(
                {member: HttpMetaStorePeer(urls[member], client) for member in _MEMBERS}
            )
            first = await metastore.compare_and_set(0, _chain("segment-a"))
            if not isinstance(first, Applied):
                raise TypeError("initial Paxos CAS unexpectedly lost")
            events.append(_event("cas_decided", {"version": first.snapshot.version}))

            stopped = "meta-2"
            await cluster.stop(stopped)
            events.append(_event("peer_stopped", {"node_id": stopped}))

            second = await metastore.compare_and_set(1, _chain("segment-b"))
            if not isinstance(second, Applied):
                raise TypeError("quorum Paxos CAS unexpectedly lost")
            events.append(
                _event(
                    "cas_decided_with_one_peer_down",
                    {"version": second.snapshot.version},
                )
            )
            observed = await metastore.read()
            events.append(_event("barrier_read", {"version": observed.version}))

            await cluster.start(stopped)
            await _wait_until(
                lambda: health(stopped),
                timeout=timeout,
                description=f"restarted {stopped}",
            )
            events.append(_event("peer_restarted", {"node_id": stopped}))

            restarted_peer = HttpMetaStorePeer(urls[stopped], client)

            async def caught_up() -> int | None:
                snapshot = await restarted_peer.read()
                return snapshot.version if snapshot.version == 2 else None

            version = await _wait_until(
                caught_up,
                timeout=timeout,
                description=f"{stopped} Paxos catch-up",
            )
            if not isinstance(version, int):
                raise TypeError("catch-up version has an invalid type")
            events.append(
                _event(
                    "peer_caught_up",
                    {"node_id": stopped, "version": version},
                )
            )
            return events
        finally:
            await cluster.close()


async def run_demo(
    runtime_dir: Path | None = None,
    *,
    timeout: float = 10.0,
) -> list[LabEvent]:
    if runtime_dir is not None:
        return await _run_demo(runtime_dir, timeout)
    with tempfile.TemporaryDirectory(prefix="delos-paxos-metastore-") as temporary:
        return await _run_demo(Path(temporary), timeout)


def main() -> None:
    for event in asyncio.run(run_demo()):
        print(event.model_dump_json())


if __name__ == "__main__":
    main()
