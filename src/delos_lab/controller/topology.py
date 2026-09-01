import asyncio
import time
from typing import Protocol

import httpx
from pydantic import ValidationError

from .deployment import (
    DatabaseNodeObservation,
    DeploymentSnapshot,
    MetaStoreNodeObservation,
    NodeDirectory,
    NodeObservation,
    NodeProcessState,
    NodeRecord,
    ServiceName,
    ServiceObservation,
)


class NodeStateReader(Protocol):
    async def states(
        self,
        node_ids: tuple[str, ...],
    ) -> dict[str, NodeProcessState]: ...


class DeploymentCollector:
    """Project typed node observations; never decide or mutate Delos state."""

    def __init__(
        self,
        directory: NodeDirectory,
        lifecycle: NodeStateReader,
        client: httpx.AsyncClient,
        *,
        observation_timeout: float = 1.5,
    ) -> None:
        self._directory = directory
        self._lifecycle = lifecycle
        self._client = client
        self._observation_timeout = observation_timeout

    async def _observe(
        self,
        node: NodeRecord,
        runtime: NodeProcessState,
    ) -> NodeObservation:
        reachable = False
        metastore: MetaStoreNodeObservation | None = None
        database: DatabaseNodeObservation | None = None
        observation_error: str | None = None
        if runtime.lifecycle == "running":
            try:
                async with asyncio.timeout(self._observation_timeout):
                    health = await self._client.get(f"{node.endpoint}/health")
                    health.raise_for_status()
                    state_path = "/paxos/state" if node.service == "meta" else "/state"
                    state = await self._client.get(f"{node.endpoint}{state_path}")
                    state.raise_for_status()
                    reachable = True
                    if node.service == "meta":
                        metastore = MetaStoreNodeObservation.model_validate(state.json())
                    else:
                        database = DatabaseNodeObservation.model_validate(state.json())
            except (TimeoutError, httpx.HTTPError, ValidationError) as error:
                observation_error = str(error)
                reachable = False
        return NodeObservation(
            node_id=node.node_id,
            service=node.service,
            lifecycle=runtime.lifecycle,
            reachable=reachable,
            metastore=metastore,
            database=database,
            error=observation_error,
            observed_at=time.time(),
        )

    async def collect(self) -> DeploymentSnapshot:
        nodes = self._directory.nodes()
        lifecycle = await self._lifecycle.states(tuple(node.node_id for node in nodes))
        observed = await asyncio.gather(
            *(self._observe(node, lifecycle[node.node_id]) for node in nodes)
        )
        observations = {observation.node_id: observation for observation in observed}

        service_names: tuple[ServiceName, ...] = ("meta", "db")
        services = {
            service: ServiceObservation(
                name=service,
                configured=self._directory.configured[service],
                running=sum(
                    node.service == service and lifecycle[node.node_id].running for node in nodes
                ),
                reachable=sum(
                    node.service == service and observations[node.node_id].reachable
                    for node in nodes
                ),
            )
            for service in service_names
        }
        return DeploymentSnapshot(
            services=services,
            nodes=observations,
            collected_at=time.time(),
        )
