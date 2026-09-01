from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

LogPosition = Annotated[int, Field(ge=0)]
ChainVersion = Annotated[int, Field(ge=0)]


class LogletConfiguration(BaseModel):
    """Opaque, versioned configuration interpreted only by its Loglet adapter."""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class LogSegment(BaseModel):
    """One half-open VirtualLog address range: ``[virtual_start, virtual_stop)``."""

    model_config = ConfigDict(frozen=True)

    segment_id: str = Field(min_length=1)
    virtual_start: LogPosition
    virtual_stop: LogPosition | None
    loglet: LogletConfiguration

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.virtual_stop is not None and self.virtual_stop < self.virtual_start:
            raise ValueError("virtual stop cannot precede virtual start")
        return self


class NewLogletConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str = Field(min_length=1)
    loglet: LogletConfiguration

    def activate(self, virtual_start: int) -> LogSegment:
        return LogSegment(
            segment_id=self.segment_id,
            virtual_start=virtual_start,
            virtual_stop=None,
            loglet=self.loglet,
        )


class LogletConfigurationUpdate(BaseModel):
    """Opaque replacement configuration for one sealed LogChain segment."""

    model_config = ConfigDict(frozen=True)

    segment_id: str = Field(min_length=1)
    loglet: LogletConfiguration


class LogChain(BaseModel):
    model_config = ConfigDict(frozen=True)

    segments: tuple[LogSegment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_chain(self) -> Self:
        if len({segment.segment_id for segment in self.segments}) != len(self.segments):
            raise ValueError("segment identifiers must be unique")

        for previous, current in zip(self.segments[:-1], self.segments[1:], strict=True):
            if previous.virtual_stop is None:
                raise ValueError("only the last segment may have an open stop")
            if current.virtual_start != previous.virtual_stop:
                raise ValueError("segments must be contiguous")

        if self.segments[-1].virtual_stop is not None:
            raise ValueError("last segment must have an open stop")
        return self

    @property
    def active(self) -> LogSegment:
        return self.segments[-1]

    def segment_at(self, position: int) -> LogSegment | None:
        if position < 0:
            return None
        for segment in self.segments:
            if segment.virtual_start <= position and (
                segment.virtual_stop is None or position < segment.virtual_stop
            ):
                return segment
        return None


class VirtualLogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    position: LogPosition
    command_id: str
    payload: bytes
    segment_id: str
    local_position: LogPosition


class VersionedLogChain(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: ChainVersion
    chain: LogChain | None

    @model_validator(mode="after")
    def validate_initial_state(self) -> Self:
        if (self.version == 0) != (self.chain is None):
            raise ValueError("only version zero may have no chain")
        return self
