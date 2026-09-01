import asyncio
from typing import Protocol
from uuid import uuid4

from .loglet import LogletProvider, LogletSealed, LogletTail, LogletUnavailable
from .metastore import Applied, MetaStore
from .types import (
    LogChain,
    LogletConfigurationUpdate,
    LogSegment,
    NewLogletConfiguration,
    VersionedLogChain,
    VirtualLogEntry,
)


class ChainUnavailable(Exception):
    """설치된 LogChain을 아직 관찰하지 못했다."""


class PositionUnavailable(Exception):
    """요청한 가상 위치에 커밋된 엔트리가 없다."""


class ReconfigurationPolicy(Protocol):
    """Choose a replacement for a failed, but not merely sealed, Loglet."""

    async def successor(self, failed: LogSegment) -> NewLogletConfiguration: ...


class VirtualLog:
    def __init__(
        self,
        meta_store: MetaStore,
        loglets: LogletProvider | None = None,
        reconfiguration: ReconfigurationPolicy | None = None,
        *,
        roll_forward_timeout: float = 0.1,
        max_reconfiguration_attempts: int = 3,
    ) -> None:
        if roll_forward_timeout < 0:
            raise ValueError("roll-forward timeout cannot be negative")
        if max_reconfiguration_attempts < 1:
            raise ValueError("max reconfiguration attempts must be positive")
        self._meta_store = meta_store
        self._loglets = loglets
        self._reconfiguration = reconfiguration
        self._roll_forward_timeout = roll_forward_timeout
        self._max_reconfiguration_attempts = max_reconfiguration_attempts
        self._reconfigure_lock = asyncio.Lock()
        self._cached: VersionedLogChain | None = None

    @property
    def cached(self) -> VersionedLogChain:
        if self._cached is None or self._cached.chain is None:
            raise ChainUnavailable("VirtualLog has no installed chain")
        return self._cached

    async def bootstrap(self, initial_segment: LogSegment) -> VersionedLogChain:
        if initial_segment.virtual_start != 0 or initial_segment.virtual_stop is not None:
            raise ValueError("initial segment must be open and start at position zero")

        candidate = LogChain(segments=(initial_segment,))
        result = await self._meta_store.compare_and_set(0, candidate)
        snapshot = result.snapshot if isinstance(result, Applied) else result.current
        if snapshot.chain is None:
            raise ChainUnavailable("MetaStore did not return an installed chain")
        self._cached = snapshot
        return snapshot

    async def refresh(self) -> VersionedLogChain:
        snapshot = await self._meta_store.read()
        if snapshot.chain is None:
            raise ChainUnavailable("MetaStore has no installed chain")
        self._cached = snapshot
        return snapshot

    async def append(self, command_id: str, payload: bytes) -> int:
        for attempt in range(self._max_reconfiguration_attempts):
            snapshot = self.cached
            if self._loglets is None or snapshot.chain is None:
                raise LogletUnavailable("VirtualLog has no Loglet provider")
            active = snapshot.chain.active
            try:
                result = await self._loglets.get(active).append(command_id, payload)
                return active.virtual_start + result.position
            except LogletSealed:
                if attempt + 1 == self._max_reconfiguration_attempts:
                    raise
                await self._roll_forward(active)
            except LogletUnavailable:
                if (
                    attempt + 1 == self._max_reconfiguration_attempts
                    or self._reconfiguration is None
                ):
                    raise
                await self._replace_unavailable(active)
        raise AssertionError("append retry loop exhausted without returning")

    async def seal(self) -> None:
        """Seal the active Loglet in the currently cached LogChain.

        This is the VirtualLog form of the paper's shared ``seal`` contract.
        Installing a successor remains a separate control-plane operation in
        :meth:`reconfig_extend`.
        """
        snapshot = self.cached
        if self._loglets is None or snapshot.chain is None:
            raise LogletUnavailable("VirtualLog has no Loglet provider")
        await self._loglets.get(snapshot.chain.active).seal()

    async def check_tail(self) -> LogletTail:
        for attempt in range(self._max_reconfiguration_attempts):
            snapshot = self.cached
            if self._loglets is None or snapshot.chain is None:
                raise LogletUnavailable("VirtualLog has no Loglet provider")
            active = snapshot.chain.active
            try:
                local = await self._loglets.get(active).check_tail()
            except LogletUnavailable:
                if (
                    attempt + 1 == self._max_reconfiguration_attempts
                    or self._reconfiguration is None
                ):
                    raise
                await self._replace_unavailable(active)
                continue
            result = LogletTail(
                tail=active.virtual_start + local.tail,
                sealed=local.sealed,
            )
            if not local.sealed:
                return result
            if attempt + 1 == self._max_reconfiguration_attempts:
                return result
            await self._roll_forward(active)
        raise AssertionError("checkTail retry loop exhausted without returning")

    async def _roll_forward(self, sealed: LogSegment) -> None:
        """Adopt a completed reconfiguration or finish an incomplete one.

        A sealed active segment means another client may be between the seal
        and MetaStore-install steps. After a grace period, this client installs
        a fresh segment with the same opaque Loglet configuration.
        """
        async with self._reconfigure_lock:
            active = await self._refresh_if_active(sealed)
            if active is None:
                return
            await asyncio.sleep(self._roll_forward_timeout)
            active = await self._refresh_if_active(sealed)
            if active is None:
                return
            await self.reconfig_extend(
                NewLogletConfiguration(
                    segment_id=str(uuid4()),
                    loglet=active.loglet,
                )
            )

    async def _replace_unavailable(self, failed: LogSegment) -> None:
        """Use a Loglet-specific policy to replace an unavailable active segment."""
        async with self._reconfigure_lock:
            active = await self._refresh_if_active(failed)
            if active is None:
                return
            if self._reconfiguration is None:
                raise LogletUnavailable("no Loglet reconfiguration policy is installed")
            successor = await self._reconfiguration.successor(active)
            await self.reconfig_extend(successor)

    async def _refresh_if_active(self, expected: LogSegment) -> LogSegment | None:
        cached = self.cached
        if cached.chain is None:
            raise ChainUnavailable("VirtualLog has no installed chain")
        if cached.chain.active.segment_id != expected.segment_id:
            return None
        refreshed = await self.refresh()
        if refreshed.chain is None:
            raise ChainUnavailable("MetaStore has no installed chain")
        if refreshed.chain.active.segment_id != expected.segment_id:
            return None
        return refreshed.chain.active

    async def read(self, position: int) -> VirtualLogEntry:
        """Read one exact virtual position as a convenience over ``read_next``."""
        if position < 0:
            raise PositionUnavailable(position)
        entry = await self.read_next(position, position + 1)
        if entry is None:
            raise PositionUnavailable(position)
        return entry

    async def read_next(self, virtual_start: int, virtual_stop: int) -> VirtualLogEntry | None:
        """Return the first entry in the half-open virtual range.

        Routing and address translation belong here; whether positions may be
        sparse belongs to the selected Loglet implementation.
        """
        if virtual_start < 0 or virtual_stop <= virtual_start:
            raise ValueError("readNext requires 0 <= virtual_start < virtual_stop")
        snapshot = self.cached
        if self._loglets is None or snapshot.chain is None:
            raise LogletUnavailable("VirtualLog has no Loglet provider")

        for segment in snapshot.chain.segments:
            overlap_start = max(virtual_start, segment.virtual_start)
            overlap_stop = min(
                virtual_stop,
                segment.virtual_stop if segment.virtual_stop is not None else virtual_stop,
            )
            if overlap_start >= overlap_stop:
                continue
            local_start = overlap_start - segment.virtual_start
            local_stop = overlap_stop - segment.virtual_start
            entry = await self._loglets.get(segment).read_next(local_start, local_stop)
            if entry is None:
                continue
            if not local_start <= entry.position < local_stop:
                raise RuntimeError("Loglet readNext returned an entry outside the requested range")
            return VirtualLogEntry(
                position=segment.virtual_start + entry.position,
                command_id=entry.command_id,
                payload=entry.payload,
                segment_id=segment.segment_id,
                local_position=entry.position,
            )
        return None

    async def prefix_trim(self, trim_position: int) -> int:
        """Trim the VirtualLog prefix and remove fully trimmed sealed segments.

        ``trim_position`` is the first virtual position that may remain. The
        physical Loglet trim happens before a segment is removed from the
        MetaStore mapping, so a stale client can observe missing data but can
        never make discarded data authoritative again.
        """
        if trim_position < 0:
            raise ValueError("prefixTrim requires a non-negative position")
        snapshot = self.cached
        if self._loglets is None or snapshot.chain is None:
            raise LogletUnavailable("VirtualLog has no Loglet provider")
        if trim_position <= snapshot.chain.segments[0].virtual_start:
            return snapshot.chain.segments[0].virtual_start

        tail = await self.check_tail()
        if trim_position > tail.tail:
            raise ValueError("prefixTrim cannot pass the VirtualLog tail")
        snapshot = self.cached
        if snapshot.chain is None:
            raise ChainUnavailable("VirtualLog has no installed chain")

        for segment in snapshot.chain.segments:
            if trim_position <= segment.virtual_start:
                break
            virtual_target = min(
                trim_position,
                segment.virtual_stop if segment.virtual_stop is not None else trim_position,
            )
            local_target = virtual_target - segment.virtual_start
            local_result = await self._loglets.get(segment).prefix_trim(local_target)
            if local_result < local_target:
                return segment.virtual_start + local_result

        for _ in range(self._max_reconfiguration_attempts):
            current = self.cached
            if current.chain is None or len(current.chain.segments) == 1:
                break
            first = current.chain.segments[0]
            if first.virtual_stop is None or first.virtual_stop > trim_position:
                break
            await self.reconfig_truncate()
        current = self.cached
        if current.chain is None:
            raise ChainUnavailable("VirtualLog has no installed chain")
        return max(trim_position, current.chain.segments[0].virtual_start)

    async def reconfig_extend(
        self,
        next_configuration: NewLogletConfiguration,
    ) -> bool:
        """Implement the paper's reconfigExtend safety order (section 3.2).

        ``check_tail().tail`` is the first unwritten local position, so its
        virtual translation is both the old half-open stop and the new start.
        Only MetaStore CAS publishes this candidate as an installed LogChain.
        """
        snapshot = self.cached
        if self._loglets is None or snapshot.chain is None:
            raise LogletUnavailable("VirtualLog has no Loglet provider")

        active = snapshot.chain.active
        runtime = self._loglets.get(active)
        await runtime.seal()
        tail_result = await runtime.check_tail()
        if not tail_result.sealed:
            raise ChainUnavailable("Loglet remained open after seal")

        virtual_stop = active.virtual_start + tail_result.tail
        closed = active.model_copy(update={"virtual_stop": virtual_stop})
        next_segment = next_configuration.activate(virtual_stop)
        candidate = LogChain(segments=(*snapshot.chain.segments[:-1], closed, next_segment))
        return await self._install(snapshot, candidate)

    async def reconfig_truncate(self) -> bool:
        """Physically trim and remove the first sealed LogChain segment."""
        snapshot = self.cached
        if self._loglets is None or snapshot.chain is None:
            raise LogletUnavailable("VirtualLog has no Loglet provider")
        if len(snapshot.chain.segments) == 1:
            return False
        first = snapshot.chain.segments[0]
        if first.virtual_stop is None:
            raise ChainUnavailable("reconfigTruncate requires a sealed first segment")
        local_stop = first.virtual_stop - first.virtual_start
        trimmed = await self._loglets.get(first).prefix_trim(local_stop)
        if trimmed < local_stop:
            return False
        candidate = LogChain(segments=snapshot.chain.segments[1:])
        return await self._install(snapshot, candidate)

    async def reconfig_modify(self, update: LogletConfigurationUpdate) -> bool:
        """Replace the opaque configuration of one sealed segment.

        The Loglet-specific caller must ensure the replacement configuration
        serves the same entries for the segment's existing virtual range.
        VirtualLog intentionally cannot inspect or prove that data-plane fact.
        """
        snapshot = self.cached
        if snapshot.chain is None:
            raise ChainUnavailable("VirtualLog has no installed chain")
        index = next(
            (
                index
                for index, segment in enumerate(snapshot.chain.segments)
                if segment.segment_id == update.segment_id
            ),
            None,
        )
        if index is None:
            raise ValueError(f"unknown LogChain segment {update.segment_id}")
        selected = snapshot.chain.segments[index]
        if selected.virtual_stop is None:
            raise ValueError("reconfigModify cannot replace the active segment")
        if selected.loglet == update.loglet:
            return True
        modified = selected.model_copy(update={"loglet": update.loglet})
        segments = list(snapshot.chain.segments)
        segments[index] = modified
        return await self._install(snapshot, LogChain(segments=tuple(segments)))

    async def _install(self, expected: VersionedLogChain, candidate: LogChain) -> bool:
        result = await self._meta_store.compare_and_set(expected.version, candidate)
        installed = result.snapshot if isinstance(result, Applied) else result.current
        if installed.chain is None:
            raise ChainUnavailable("MetaStore did not return an installed chain")
        self._cached = installed
        return isinstance(result, Applied)
