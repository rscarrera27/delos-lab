def quorum_size(count: int) -> int:
    if count < 3:
        raise ValueError("membership size must be at least three")
    return count // 2 + 1


def validate_fixed_members(
    members: tuple[str, ...],
    *,
    label: str,
) -> tuple[str, ...]:
    quorum_size(len(members))
    if len(set(members)) != len(members) or any(not member for member in members):
        raise ValueError(f"{label} members must be unique non-empty identifiers")
    return members
