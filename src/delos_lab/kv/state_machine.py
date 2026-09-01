from collections.abc import Mapping
from typing import assert_never

from .types import CompareAndSet, Delete, Increment, KvOperation, KvResult, KvValue, Put


class KvStateMachine:
    def __init__(self, values: Mapping[str, KvValue] | None = None) -> None:
        self._values = dict(values or {})

    @property
    def snapshot(self) -> dict[str, KvValue]:
        return dict(self._values)

    def restore(self, values: Mapping[str, KvValue]) -> None:
        self._values = dict(values)

    def get(self, key: str) -> KvValue | None:
        return self._values.get(key)

    def apply(self, operation: KvOperation) -> KvResult:
        if isinstance(operation, Put):
            self._values[operation.key] = operation.value
            return KvResult(code="APPLIED", value=operation.value)
        if isinstance(operation, Delete):
            if operation.key not in self._values:
                return KvResult(code="NOT_FOUND")
            return KvResult(code="APPLIED", value=self._values.pop(operation.key))
        if isinstance(operation, CompareAndSet):
            current = self._values.get(operation.key)
            if current != operation.expected:
                return KvResult(code="CAS_MISMATCH", value=current)
            self._values[operation.key] = operation.value
            return KvResult(code="APPLIED", value=operation.value)
        if isinstance(operation, Increment):
            current = self._values.get(operation.key, 0)
            if not isinstance(current, int):
                return KvResult(code="TYPE_MISMATCH", value=current)
            value = current + operation.delta
            self._values[operation.key] = value
            return KvResult(code="APPLIED", value=value)
        assert_never(operation)
