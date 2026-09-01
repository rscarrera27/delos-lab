from typing import Protocol
from uuid import uuid4

from delos_lab.virtual_log.loglet import LogletUnavailable
from delos_lab.virtual_log.types import NewLogletConfiguration, VersionedLogChain

from .config import NativeLogletConfiguration, native_loglet_configuration
from .reconfiguration import IncarnationDirectory


class ReconfigurableVirtualLog(Protocol):
    @property
    def cached(self) -> VersionedLogChain: ...

    async def refresh(self) -> VersionedLogChain: ...

    async def reconfig_extend(self, next_configuration: NewLogletConfiguration) -> bool: ...


class EligibleStorageMembers(Protocol):
    def active_members(self) -> tuple[str, ...]: ...


class NativeLogletStorageMembership:
    """Admit a prepared Converged node by creating a new NativeLoglet segment."""

    def __init__(
        self,
        node_id: str,
        virtual_log: ReconfigurableVirtualLog,
        incarnations: IncarnationDirectory,
        eligible_members: EligibleStorageMembers | None = None,
        *,
        max_attempts: int = 3,
    ) -> None:
        self._node_id = node_id
        self._virtual_log = virtual_log
        self._incarnations = incarnations
        self._eligible_members = eligible_members
        self._max_attempts = max_attempts

    async def join(self) -> VersionedLogChain:
        incarnation = await self._incarnations.incarnation(self._node_id)
        if incarnation is None:
            raise LogletUnavailable(f"joining LogServer {self._node_id} is unavailable")

        for _ in range(self._max_attempts):
            snapshot = await self._virtual_log.refresh()
            if snapshot.chain is None:
                raise LogletUnavailable("NativeLoglet membership requires an installed LogChain")
            current = NativeLogletConfiguration.from_generic(snapshot.chain.active.loglet)
            if self._node_id in current.storage_members:
                return snapshot
            eligible = (
                set(self._eligible_members.active_members())
                if self._eligible_members is not None
                else set(current.storage_members) | {self._node_id}
            )
            members = tuple(member for member in current.storage_members if member in eligible)
            if self._node_id not in members:
                members = (*members, self._node_id)
            installed = await self._virtual_log.reconfig_extend(
                NewLogletConfiguration(
                    segment_id=str(uuid4()),
                    loglet=native_loglet_configuration(
                        members,
                        self._node_id,
                        incarnation,
                    ),
                )
            )
            if installed:
                return self._virtual_log.cached
        raise LogletUnavailable(f"could not admit LogServer {self._node_id}")
