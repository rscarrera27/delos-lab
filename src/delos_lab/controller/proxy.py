from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from .deployment import NodeRecord


class NodeProxyError(Exception):
    code = "NODE_PROXY_ERROR"
    status_code = 500


class UnsupportedNodeService(NodeProxyError):
    code = "UNSUPPORTED_NODE_SERVICE"
    status_code = 422


class NodeUnavailable(NodeProxyError):
    code = "NODE_UNAVAILABLE"
    status_code = 503


class NodeProxyDirectory(Protocol):
    def require(self, node_id: str) -> NodeRecord: ...


class ProxyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status_code: int
    body: bytes
    content_type: str | None


class NodeProxy:
    def __init__(self, directory: NodeProxyDirectory, client: httpx.AsyncClient) -> None:
        self._directory = directory
        self._client = client

    async def forward(
        self,
        node_id: str,
        *,
        method: str,
        path: str,
        query: str = "",
        body: bytes = b"",
        content_type: str | None = None,
    ) -> ProxyResponse:
        node = self._directory.require(node_id)
        if node.service != "db":
            raise UnsupportedNodeService(f"node {node_id} belongs to {node.service}, not db")
        headers = {"content-type": content_type} if content_type is not None else None
        try:
            response = await self._client.request(
                method,
                f"{node.endpoint}{path}",
                params=query,
                content=body,
                headers=headers,
            )
        except httpx.HTTPError as error:
            raise NodeUnavailable(f"node {node_id} is unavailable") from error
        return ProxyResponse(
            status_code=response.status_code,
            body=response.content,
            content_type=response.headers.get("content-type"),
        )
