import asyncio
import socket
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx


def available_port() -> int:
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


async def wait_until[T](
    operation: Callable[[], Awaitable[T | None]], *, timeout: float = 20.0
) -> T:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            result = await operation()
            if result is not None:
                return result
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            last_error = error
        await asyncio.sleep(0.05)
    raise TimeoutError(f"condition was not met: {last_error}")


async def test_controller_supervises_local_processes_and_manual_failover(
    tmp_path: Path,
) -> None:
    port = available_port()
    url = f"http://127.0.0.1:{port}"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "delos_lab.controller.process",
        "--runtime-dir",
        str(tmp_path),
        "--port",
        str(port),
        "--no-auto-start",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            await wait_until(
                lambda: _healthy(client, url),
            )
            assert (await client.post(f"{url}/api/cluster/start")).status_code == 204
            deployment = await wait_until(lambda: _reachable_deployment(client, url))
            assert all(service["reachable"] == 3 for service in deployment["services"].values())
            database_ids = tuple(
                node_id for node_id, node in deployment["nodes"].items() if node["service"] == "db"
            )

            put = await client.put(
                f"{url}/api/nodes/{database_ids[-1]}/kv/count",
                json={"client_id": "controller-test", "request_id": "put", "value": 1},
            )
            assert put.status_code == 200, put.text

            added = await client.post(f"{url}/api/database-nodes")
            assert added.status_code == 201, added.text
            added_id = added.json()["node_id"]
            assert added_id.startswith("db-")
            assert len(added_id) == 8
            await wait_until(lambda: _node_reachable(client, url, added_id))
            bootstrapped = await client.get(f"{url}/api/nodes/{added_id}/kv/count")
            assert bootstrapped.status_code == 200, bootstrapped.text
            assert bootstrapped.json()["value"] == 1

            deployment = await wait_until(lambda: _chain_at_least(client, url, 1))
            active = _latest_chain(deployment)["chain"]["segments"][-1]
            all_database_ids = (*database_ids, added_id)
            assert set(active["loglet"]["parameters"]["storage_members"]) == set(all_database_ids)
            sequencer = active["loglet"]["parameters"]["sequencer_node"]
            assert (await client.post(f"{url}/api/nodes/{sequencer}/pause")).status_code == 204
            paused = await _deployment(client, url)
            assert paused["nodes"][sequencer]["lifecycle"] == "paused"
            assert (await client.post(f"{url}/api/nodes/{sequencer}/resume")).status_code == 204
            await wait_until(lambda: _node_reachable(client, url, sequencer))

            target = next(node for node in all_database_ids if node != sequencer)
            assert (await client.post(f"{url}/api/nodes/{sequencer}/kill")).status_code == 204
            without_killed = await _deployment(client, url)
            assert sequencer not in without_killed["nodes"]
            increment = await client.post(
                f"{url}/api/nodes/{target}/kv/count/increment",
                json={"client_id": "controller-test", "request_id": "increment", "delta": 1},
            )
            assert increment.status_code == 200, increment.text
            assert increment.json()["value"] == 2
            deployment = await wait_until(lambda: _chain_at_least(client, url, 3))
            successor = _latest_chain(deployment)["chain"]["segments"][-1]
            assert successor["loglet"]["parameters"]["sequencer_node"] != sequencer

            replacement = await client.post(f"{url}/api/database-nodes")
            assert replacement.status_code == 201, replacement.text
            replacement_id = replacement.json()["node_id"]
            await wait_until(lambda: _node_reachable(client, url, replacement_id))
            deployment = await wait_until(lambda: _chain_at_least(client, url, 4))
            replaced = _latest_chain(deployment)["chain"]["segments"][-1]
            replacement_members = replaced["loglet"]["parameters"]["storage_members"]
            assert sequencer not in replacement_members
            assert replacement_id in replacement_members
            assert len(replacement_members) == len(all_database_ids)
            recovered = await client.get(f"{url}/api/nodes/{replacement_id}/kv/count")
            assert recovered.status_code == 200, recovered.text
            assert recovered.json()["value"] == 2

            reset = await client.post(f"{url}/api/cluster/reset")
            assert reset.status_code == 204, reset.text
            fresh = await wait_until(lambda: _reachable_deployment(client, url))
            fresh_database_ids = {
                node_id for node_id, node in fresh["nodes"].items() if node["service"] == "db"
            }
            assert len(fresh_database_ids) == 3
            assert fresh_database_ids.isdisjoint({*all_database_ids, replacement_id})
            assert _latest_chain(fresh) == {"version": 0, "chain": None}
            empty = await client.get(f"{url}/api/nodes/{next(iter(fresh_database_ids))}/kv/count")
            assert empty.status_code == 200, empty.text
            assert empty.json()["value"] is None
            assert (await client.post(f"{url}/api/cluster/stop")).status_code == 204
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except TimeoutError:
            process.kill()
            await process.wait()


async def _healthy(client: httpx.AsyncClient, url: str) -> bool | None:
    response = await client.get(f"{url}/api/health")
    return True if response.status_code == 200 else None


async def _deployment(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    response = await client.get(f"{url}/api/deployment")
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


async def _reachable_deployment(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    deployment = await _deployment(client, url)
    services = deployment["services"]
    if not isinstance(services, dict):
        return None
    return deployment if all(service["reachable"] == 3 for service in services.values()) else None


def _latest_chain(deployment: dict[str, Any]) -> dict[str, Any]:
    nodes = deployment["nodes"]
    if not isinstance(nodes, dict):
        raise TypeError("nodes must be an object")
    snapshots = [
        node["metastore"]["state"]["state_machine"]
        for node in nodes.values()
        if node["service"] == "meta" and node["metastore"] is not None
    ]
    return max(snapshots, key=lambda snapshot: snapshot["version"])


async def _chain_at_least(
    client: httpx.AsyncClient,
    url: str,
    version: int,
) -> dict[str, Any] | None:
    deployment = await _deployment(client, url)
    return deployment if _latest_chain(deployment)["version"] >= version else None


async def _node_reachable(
    client: httpx.AsyncClient,
    url: str,
    node_id: str,
) -> bool | None:
    deployment = await _deployment(client, url)
    nodes = deployment["nodes"]
    return True if nodes[node_id]["reachable"] else None
