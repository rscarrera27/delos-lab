from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Position = Annotated[int, Field(ge=0)]


class LogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str
    position: Position
    command_id: str
    payload: bytes


class LogletWriteRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry: LogEntry
    known_tail: int = Field(ge=0)


class KnownTailRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    known_tail: int = Field(ge=0)


class LogServerState(BaseModel):
    segment_id: str
    local_tail: int = Field(ge=0)
    trimmed_prefix: int = Field(default=0, ge=0)
    known_tail: int = Field(ge=0)
    sealed: bool


class AppendResult(BaseModel):
    status: Literal["committed"] = "committed"
    position: Position
    known_tail: int = Field(default=0, ge=0)


class CheckTailResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tail: int = Field(ge=0)
    sealed: bool
