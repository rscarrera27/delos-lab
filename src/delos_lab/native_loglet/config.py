from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from delos_lab.common.membership import validate_fixed_members
from delos_lab.virtual_log.types import LogletConfiguration


class NativeLogletConfiguration(BaseModel):
    """Configuration understood by the NativeLoglet adapter, not VirtualLog."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["native"] = "native"
    version: Literal[1] = 1
    storage_members: tuple[str, ...]
    sequencer_node: str = Field(min_length=1)
    sequencer_incarnation: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_native_configuration(self) -> Self:
        validate_fixed_members(self.storage_members, label="NativeLoglet storage")
        if self.sequencer_node not in self.storage_members:
            raise ValueError("NativeLoglet sequencer must be a storage member")
        return self

    def to_generic(self) -> LogletConfiguration:
        return LogletConfiguration(
            kind=self.kind,
            version=self.version,
            parameters={
                "storage_members": list(self.storage_members),
                "sequencer_node": self.sequencer_node,
                "sequencer_incarnation": self.sequencer_incarnation,
            },
        )

    @classmethod
    def from_generic(cls, configuration: LogletConfiguration) -> NativeLogletConfiguration:
        if configuration.kind != "native" or configuration.version != 1:
            raise ValueError(
                f"unsupported Loglet configuration: {configuration.kind}@{configuration.version}"
            )
        return cls.model_validate(
            {
                "kind": configuration.kind,
                "version": configuration.version,
                **configuration.parameters,
            }
        )


def native_loglet_configuration(
    storage_members: tuple[str, ...],
    sequencer_node: str,
    sequencer_incarnation: str,
) -> LogletConfiguration:
    return NativeLogletConfiguration(
        storage_members=storage_members,
        sequencer_node=sequencer_node,
        sequencer_incarnation=sequencer_incarnation,
    ).to_generic()
