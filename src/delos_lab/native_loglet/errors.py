class LogletError(Exception):
    """NativeLoglet 연산의 기반 오류."""


class EntryConflict(LogletError):
    pass


class PredecessorUnavailable(LogletError):
    pass


class PositionTrimmed(LogletError):
    pass


class SegmentSealed(LogletError):
    pass


class NoQuorum(LogletError):
    pass


class TailUnavailable(LogletError):
    pass


class NotSequencer(LogletError):
    pass


class IncarnationMismatch(LogletError):
    pass


class SequencerUnavailable(LogletError):
    pass
