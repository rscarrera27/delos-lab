__all__ = [
    "InvalidKvPayload",
    "KvError",
    "ReconfigurationUnavailable",
    "SyncRequired",
]


class KvError(Exception):
    """복제 KV 애플리케이션의 기반 오류."""


class ReconfigurationUnavailable(KvError):
    pass


class SyncRequired(KvError):
    pass


class InvalidKvPayload(KvError):
    pass
