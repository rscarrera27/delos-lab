from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .manifest import LabManifest, NodeManifest

type ServiceName = Literal["meta", "db"]
type ProcessLifecycle = Literal["running", "paused", "exited", "removed"]


class NodeProcessState(BaseModel):
    model_config = ConfigDict(frozen=True)

    lifecycle: ProcessLifecycle
    running: bool
    returncode: int | None = None


class NodeRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str = Field(min_length=1)
    service: ServiceName
    endpoint: str = Field(min_length=1)
    database: Path


class ServiceObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: ServiceName
    configured: int = Field(ge=0)
    running: int = Field(ge=0)
    reachable: int = Field(
        ge=0,
        description="Processes with a controller-validated component observation",
    )


class ObservedLogletConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    version: int = Field(ge=1)
    parameters: dict[str, JsonValue]


class ObservedLogSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str
    virtual_start: int = Field(ge=0)
    virtual_stop: int | None = Field(default=None, ge=0)
    loglet: ObservedLogletConfiguration


class ObservedLogChain(BaseModel):
    model_config = ConfigDict(frozen=True)

    segments: tuple[ObservedLogSegment, ...]


class ObservedChainSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=0)
    chain: ObservedLogChain | None


class MetaStoreStateObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    last_applied: int = Field(ge=0)
    state_machine: ObservedChainSnapshot


class MetaStoreNodeObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str
    state: MetaStoreStateObservation


class ProcessObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["online"]
    incarnation_id: str


class ApplicationObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    applied_position: int | None = Field(default=None, ge=0)
    values: dict[str, JsonValue]
    request_count: int = Field(ge=0)


class VirtualLogObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    chain_version: int | None
    cached_chain: ObservedChainSnapshot | None
    active_segment: str | None
    active_virtual_start: int | None
    known_virtual_tail: int | None = Field(
        description=(
            "This client's NativeLoglet knownTail translated into the VirtualLog address "
            "space; a possibly stale lower bound, not a fresh checkTail result."
        )
    )


class TailObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    tail: int = Field(ge=0)
    sealed: bool


class NativeLogletClientObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str
    known_tail: int = Field(ge=0)
    last_check_tail: TailObservation | None


class NativeSequencerObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str
    known_tail: int = Field(ge=0)


class LogServerSegmentObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str
    local_tail: int = Field(ge=0)
    trimmed_prefix: int = Field(ge=0)
    known_tail: int = Field(ge=0)
    sealed: bool


class LogServerObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    segments: tuple[LogServerSegmentObservation, ...]


class DatabaseNodeObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str
    process: ProcessObservation
    application: ApplicationObservation
    virtual_log: VirtualLogObservation
    native_loglet_client: NativeLogletClientObservation | None
    sequencer: NativeSequencerObservation | None
    log_server: LogServerObservation


class NodeObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str = Field(min_length=1)
    service: ServiceName
    lifecycle: ProcessLifecycle
    reachable: bool = Field(
        description=(
            "Controller fetched and validated this node's component state; "
            "this does not imply protocol quorum availability"
        )
    )
    metastore: MetaStoreNodeObservation | None = None
    database: DatabaseNodeObservation | None = None
    error: str | None = None
    observed_at: float

    @model_validator(mode="after")
    def validate_observed_component(self) -> NodeObservation:
        observations = int(self.metastore is not None) + int(self.database is not None)
        if self.reachable != (observations == 1):
            raise ValueError("reachable nodes require exactly one typed component observation")
        if self.service == "meta" and self.database is not None:
            raise ValueError("MetaStore node cannot expose a database observation")
        if self.service == "db" and self.metastore is not None:
            raise ValueError("database node cannot expose a MetaStore observation")
        return self


class DeploymentSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    services: dict[ServiceName, ServiceObservation]
    nodes: dict[str, NodeObservation]
    collected_at: float


class NodeDirectory(Protocol):
    @property
    def configured(self) -> dict[ServiceName, int]: ...

    @property
    def manifest(self) -> LabManifest: ...

    def nodes(self, service: ServiceName | None = None) -> tuple[NodeRecord, ...]: ...

    def require(self, node_id: str) -> NodeRecord: ...


class StaticNodeDirectory:
    def __init__(self, manifest: LabManifest) -> None:
        self._manifest = manifest

    @staticmethod
    def _record(node: NodeManifest) -> NodeRecord:
        return NodeRecord(
            node_id=node.node_id,
            service="meta" if node.group == "metastore" else "db",
            endpoint=node.endpoint,
            database=node.database,
        )

    @property
    def configured(self) -> dict[ServiceName, int]:
        return {
            "meta": len(self._manifest.metastore_nodes),
            "db": len(self._manifest.active_database_nodes),
        }

    @property
    def manifest(self) -> LabManifest:
        return self._manifest

    def nodes(self, service: ServiceName | None = None) -> tuple[NodeRecord, ...]:
        return tuple(
            self._record(node)
            for node in self._manifest.nodes.values()
            if not node.retired
            and (service is None or ("meta" if node.group == "metastore" else "db") == service)
        )

    def require(self, node_id: str) -> NodeRecord:
        try:
            node = self._manifest.nodes[node_id]
        except KeyError as error:
            raise ValueError(f"unknown node: {node_id}") from error
        if node.retired:
            raise ValueError(f"retired node: {node_id}")
        return self._record(node)
