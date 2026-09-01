import json
from collections.abc import Mapping
from pathlib import Path


class ManifestEndpointDirectory:
    """Resolve deployment endpoints without making them protocol membership."""

    def __init__(self, manifest: Path, fallback: Mapping[str, str]) -> None:
        self._manifest = manifest
        self._fallback = {node: endpoint.rstrip("/") for node, endpoint in fallback.items()}

    def endpoint(self, node_id: str) -> str:
        try:
            document = json.loads(self._manifest.read_text(encoding="utf-8"))
            endpoint = document["nodes"][node_id]["endpoint"]
            if not isinstance(endpoint, str) or not endpoint:
                raise ValueError(f"invalid endpoint for {node_id}")
            return endpoint.rstrip("/")
        except KeyError, OSError, TypeError, ValueError, json.JSONDecodeError:
            return self._fallback[node_id]

    def active_members(self) -> tuple[str, ...]:
        """Read deployable DB identities without treating the manifest as LogChain state."""
        try:
            document = json.loads(self._manifest.read_text(encoding="utf-8"))
            nodes = document["nodes"]
            if not isinstance(nodes, dict):
                raise TypeError("manifest nodes must be an object")
            members = tuple(
                node_id
                for node_id, record in nodes.items()
                if isinstance(node_id, str)
                and isinstance(record, dict)
                and record.get("group") == "database"
                and record.get("retired") is not True
            )
            if not members:
                raise ValueError("manifest has no active database members")
            return members
        except OSError, TypeError, ValueError, json.JSONDecodeError:
            return tuple(self._fallback)
