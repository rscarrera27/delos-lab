class PaxosError(Exception):
    """Single-slot Paxos 연산의 기반 오류."""


class PaxosNoQuorum(PaxosError):
    pass


class PaxosSafetyError(PaxosError):
    pass
