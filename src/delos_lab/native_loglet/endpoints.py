from collections.abc import Mapping
from typing import Protocol


class EndpointDirectory(Protocol):
    def endpoint(self, node_id: str) -> str: ...


class StaticEndpointDirectory:
    def __init__(self, endpoints: Mapping[str, str]) -> None:
        self._endpoints = {node: endpoint.rstrip("/") for node, endpoint in endpoints.items()}

    def endpoint(self, node_id: str) -> str:
        return self._endpoints[node_id]


def endpoint_directory(
    value: Mapping[str, str] | EndpointDirectory,
) -> EndpointDirectory:
    return StaticEndpointDirectory(value) if isinstance(value, Mapping) else value
