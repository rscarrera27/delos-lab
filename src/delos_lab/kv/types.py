from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

type KvValue = StrictStr | StrictInt


class Put(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["put"] = "put"
    key: str = Field(min_length=1)
    value: KvValue


class Delete(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["delete"] = "delete"
    key: str = Field(min_length=1)


class CompareAndSet(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["compare_and_set"] = "compare_and_set"
    key: str = Field(min_length=1)
    expected: KvValue | None
    value: KvValue


class Increment(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["increment"] = "increment"
    key: str = Field(min_length=1)
    delta: StrictInt


type KvOperation = Annotated[
    Put | Delete | CompareAndSet | Increment,
    Field(discriminator="kind"),
]


class KvCommandEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    client_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    operation: KvOperation

    @property
    def command_id(self) -> str:
        return f"{quote(self.client_id, safe='')}/{quote(self.request_id, safe='')}"

    def to_payload(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def from_payload(cls, payload: bytes) -> KvCommandEnvelope:
        return cls.model_validate_json(payload)


class KvResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: Literal["APPLIED", "NOT_FOUND", "CAS_MISMATCH", "TYPE_MISMATCH"]
    value: KvValue | None = None
