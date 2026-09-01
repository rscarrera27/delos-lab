import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .types import KvCommandEnvelope, KvResult, KvValue


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    client_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    command: KvCommandEnvelope
    result: KvResult
    first_position: int = Field(ge=0)


class SnapshotAppliedEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    position: int = Field(ge=0)
    command: KvCommandEnvelope
    result: KvResult


class KvSnapshotData(BaseModel):
    """Application state at one durable VirtualLog position."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    applied_position: int = Field(ge=-1)
    values: dict[str, KvValue]
    requests: tuple[SnapshotRequest, ...]
    applied_entries: tuple[SnapshotAppliedEntry, ...]

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        positions = tuple(entry.position for entry in self.applied_entries)
        if positions != tuple(sorted(set(positions))):
            raise ValueError("snapshot applied positions must be unique and ordered")
        if self.applied_position == -1:
            if self.values or self.requests or self.applied_entries:
                raise ValueError("an unapplied snapshot must be empty")
            return self
        if not positions or positions[-1] != self.applied_position:
            raise ValueError("snapshot must contain the result at its applied position")
        if any(request.first_position > self.applied_position for request in self.requests):
            raise ValueError("snapshot request position exceeds applied progress")
        return self

    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()


class KvSnapshot(BaseModel):
    """Checksummed transfer envelope; NativeLoglet state is intentionally absent."""

    model_config = ConfigDict(frozen=True)

    data: KvSnapshotData
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(cls, data: KvSnapshotData) -> KvSnapshot:
        return cls(data=data, sha256=data.digest())

    @model_validator(mode="after")
    def verify_checksum(self) -> Self:
        if self.sha256 != self.data.digest():
            raise ValueError("KV snapshot checksum mismatch")
        return self
