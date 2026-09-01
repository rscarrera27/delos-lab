import secrets
import string
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from delos_lab.common.membership import validate_fixed_members


class NodeManifest(BaseModel):
    model_config = ConfigDict(frozen=True)
    node_id: str = Field(min_length=1)
    group: Literal["metastore", "database"]
    endpoint: str = Field(min_length=1)
    database: Path
    join_existing_database: bool = False
    process_removed: bool = False
    retired: bool = False


_DATABASE_ID_ALPHABET = string.ascii_lowercase + string.digits


def new_database_process_id(existing: set[str]) -> str:
    """Allocate a compact opaque identity; retired identities remain reserved."""
    while True:
        candidate = "db-" + "".join(secrets.choice(_DATABASE_ID_ALPHABET) for _ in range(5))
        if candidate not in existing:
            return candidate


def new_database_process_ids(count: int, existing: set[str] | None = None) -> tuple[str, ...]:
    reserved = set() if existing is None else set(existing)
    allocated: list[str] = []
    for _ in range(count):
        node_id = new_database_process_id(reserved)
        reserved.add(node_id)
        allocated.append(node_id)
    return tuple(allocated)


class LabManifest(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    runtime_dir: Path
    nodes: dict[str, NodeManifest]

    @model_validator(mode="after")
    def validate_nodes(self) -> Self:
        if any(key != node.node_id for key, node in self.nodes.items()):
            raise ValueError("node map keys must match node identifiers")
        if any(node.retired and node.group != "database" for node in self.nodes.values()):
            raise ValueError("only database identities can be retired")
        if any(node.retired and not node.process_removed for node in self.nodes.values()):
            raise ValueError("retired database identities must have removed processes")
        validate_fixed_members(
            tuple(node.node_id for node in self.metastore_nodes),
            label="MetaStore",
        )
        validate_fixed_members(
            tuple(node.node_id for node in self.database_nodes),
            label="database",
        )
        if len({node.endpoint for node in self.nodes.values()}) != len(self.nodes):
            raise ValueError("node endpoints must be unique")
        return self

    @classmethod
    def create(
        cls,
        runtime_dir: Path,
        *,
        ports: tuple[int, ...],
        database_ids: tuple[str, ...] | None = None,
    ) -> LabManifest:
        if len(ports) != 6 or len(set(ports)) != 6 or any(port <= 0 for port in ports):
            raise ValueError("six unique positive ports are required")
        return cls.create_subprocess(
            runtime_dir,
            meta_ports=ports[:3],
            db_ports=ports[3:],
            database_ids=database_ids,
        )

    @classmethod
    def create_subprocess(
        cls,
        runtime_dir: Path,
        *,
        meta_ports: tuple[int, ...],
        db_ports: tuple[int, ...],
        database_ids: tuple[str, ...] | None = None,
    ) -> LabManifest:
        validate_fixed_members(
            tuple(f"meta-{index}" for index in range(1, len(meta_ports) + 1)),
            label="MetaStore",
        )
        database_ids = database_ids or new_database_process_ids(len(db_ports))
        if len(database_ids) != len(db_ports) or len(set(database_ids)) != len(database_ids):
            raise ValueError("database identifiers must match ports and be unique")
        if any(not node_id.startswith("db-") for node_id in database_ids):
            raise ValueError("database identifiers must start with db-")
        validate_fixed_members(database_ids, label="database")
        ports = (*meta_ports, *db_ports)
        if len(set(ports)) != len(ports) or any(port <= 0 for port in ports):
            raise ValueError("subprocess ports must be unique positive integers")
        runtime_dir = runtime_dir.resolve()
        names = (
            *(f"meta-{index}" for index in range(1, len(meta_ports) + 1)),
            *database_ids,
        )
        nodes = {
            node_id: NodeManifest(
                node_id=node_id,
                group="metastore" if node_id.startswith("meta-") else "database",
                endpoint=f"http://127.0.0.1:{port}",
                database=runtime_dir / f"{node_id}.sqlite3",
            )
            for node_id, port in zip(names, ports, strict=True)
        }
        return cls(runtime_dir=runtime_dir, nodes=nodes)

    @property
    def metastore_nodes(self) -> tuple[NodeManifest, ...]:
        return tuple(node for node in self.nodes.values() if node.group == "metastore")

    @property
    def database_nodes(self) -> tuple[NodeManifest, ...]:
        return tuple(node for node in self.nodes.values() if node.group == "database")

    @property
    def active_database_nodes(self) -> tuple[NodeManifest, ...]:
        return tuple(node for node in self.database_nodes if not node.retired)

    @property
    def path(self) -> Path:
        return self.runtime_dir / "manifest.json"

    def save(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def add_database_node(self, endpoint: str) -> NodeManifest:
        if self.pending_database_node is not None:
            raise RuntimeError("a database node join is already pending")
        node_id = new_database_process_id(set(self.nodes))
        node = NodeManifest(
            node_id=node_id,
            group="database",
            endpoint=endpoint,
            database=self.runtime_dir / f"{node_id}.sqlite3",
            join_existing_database=True,
        )
        candidate = LabManifest(runtime_dir=self.runtime_dir, nodes={**self.nodes, node_id: node})
        self.nodes = candidate.nodes
        self.save()
        return node

    @property
    def pending_database_node(self) -> NodeManifest | None:
        return next(
            (node for node in self.active_database_nodes if node.join_existing_database),
            None,
        )

    def mark_database_node_joined(self, node_id: str) -> NodeManifest:
        node = self.nodes[node_id]
        if node.group != "database":
            raise ValueError(f"not a database node: {node_id}")
        joined = node.model_copy(update={"join_existing_database": False})
        candidate = LabManifest(runtime_dir=self.runtime_dir, nodes={**self.nodes, node_id: joined})
        self.nodes = candidate.nodes
        self.save()
        return joined

    def retire_database_node(self, node_id: str) -> NodeManifest:
        node = self.nodes[node_id]
        if node.group != "database":
            raise ValueError(f"not a database node: {node_id}")
        retired = node.model_copy(
            update={
                "join_existing_database": False,
                "process_removed": True,
                "retired": True,
            }
        )
        candidate = LabManifest(
            runtime_dir=self.runtime_dir,
            nodes={**self.nodes, node_id: retired},
        )
        self.nodes = candidate.nodes
        self.save()
        return retired

    @classmethod
    def load(cls, path: Path) -> LabManifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
