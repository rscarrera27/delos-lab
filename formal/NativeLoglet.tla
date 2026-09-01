--------------------------- MODULE NativeLoglet ---------------------------
EXTENDS Integers, FiniteSets

CONSTANTS Servers, Quorum, MaxPosition

Positions == 0..MaxPosition
NoPosition == -1

VARIABLES entries, sealed, trimmed, nextPosition, pending,
          knownTail, acknowledged, sealBoundary

vars == <<entries, sealed, trimmed, nextPosition, pending,
          knownTail, acknowledged, sealBoundary>>

Covers(server, position) ==
    position < trimmed[server] \/ position \in entries[server]

CoveredByQuorum(position) ==
    Cardinality({server \in Servers : Covers(server, position)}) >= Quorum

Init ==
    /\ entries = [server \in Servers |-> {}]
    /\ sealed = {}
    /\ trimmed = [server \in Servers |-> 0]
    /\ nextPosition = 0
    /\ pending = NoPosition
    /\ knownTail = 0
    /\ acknowledged = {}
    /\ sealBoundary = NoPosition

\* The single sequencer assigns one position and retains it across retries.
StartAppend ==
    /\ pending = NoPosition
    /\ sealBoundary = NoPosition
    /\ nextPosition \in Positions
    /\ pending' = nextPosition
    /\ nextPosition' = nextPosition + 1
    /\ UNCHANGED <<entries, sealed, trimmed, knownTail,
                    acknowledged, sealBoundary>>

StorePending(server) ==
    /\ pending \in Positions
    /\ server \notin sealed
    /\ pending >= trimmed[server]
    /\ entries' = [entries EXCEPT ![server] = @ \cup {pending}]
    /\ UNCHANGED <<sealed, trimmed, nextPosition, pending,
                    knownTail, acknowledged, sealBoundary>>

\* An append is acknowledged only after the same position reaches a quorum.
CommitPending ==
    /\ pending \in Positions
    /\ CoveredByQuorum(pending)
    /\ knownTail' = pending + 1
    /\ acknowledged' = acknowledged \cup {pending}
    /\ pending' = NoPosition
    /\ UNCHANGED <<entries, sealed, trimmed, nextPosition, sealBoundary>>

\* Once a seal reaches a quorum, a partial append can fail but remain durable.
FailPending ==
    /\ pending \in Positions
    /\ Cardinality(sealed) >= Quorum
    /\ ~CoveredByQuorum(pending)
    /\ pending' = NoPosition
    /\ UNCHANGED <<entries, sealed, trimmed, nextPosition,
                    knownTail, acknowledged, sealBoundary>>

SealServer(server) ==
    /\ server \notin sealed
    /\ LET newSealed == sealed \cup {server}
       IN  /\ sealed' = newSealed
           /\ sealBoundary' =
                IF sealBoundary = NoPosition /\ Cardinality(newSealed) >= Quorum
                THEN nextPosition
                ELSE sealBoundary
    /\ UNCHANGED <<entries, trimmed, nextPosition, pending,
                    knownTail, acknowledged>>

\* Sealed repair bypasses the seal bit and may make a zombie append durable.
Repair(position, target) ==
    /\ Cardinality(sealed) >= Quorum
    /\ position \in Positions
    /\ target \in Servers
    /\ position >= trimmed[target]
    /\ \E source \in Servers : position \in entries[source]
    /\ entries' = [entries EXCEPT ![target] = @ \cup {position}]
    /\ UNCHANGED <<sealed, trimmed, nextPosition, pending,
                    knownTail, acknowledged, sealBoundary>>

\* The caller may trim only a prefix it already knows to be committed.
Trim(server, trimPosition) ==
    /\ server \in Servers
    /\ trimPosition \in trimmed[server]..knownTail
    /\ trimmed' = [trimmed EXCEPT ![server] = trimPosition]
    /\ entries' = [entries EXCEPT
                      ![server] = {position \in @ : position >= trimPosition}]
    /\ UNCHANGED <<sealed, nextPosition, pending,
                    knownTail, acknowledged, sealBoundary>>

Next ==
    \/ StartAppend
    \/ \E server \in Servers : StorePending(server)
    \/ CommitPending
    \/ FailPending
    \/ \E server \in Servers : SealServer(server)
    \/ \E position \in Positions, target \in Servers : Repair(position, target)
    \/ \E server \in Servers, trimPosition \in 0..(MaxPosition + 1):
        Trim(server, trimPosition)

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ entries \in [Servers -> SUBSET Positions]
    /\ sealed \subseteq Servers
    /\ trimmed \in [Servers -> 0..(MaxPosition + 1)]
    /\ nextPosition \in 0..(MaxPosition + 1)
    /\ pending \in Positions \cup {NoPosition}
    /\ knownTail \in 0..(MaxPosition + 1)
    /\ acknowledged \subseteq Positions
    /\ sealBoundary \in 0..(MaxPosition + 1) \cup {NoPosition}

AcknowledgedIsDense == acknowledged = 0..(knownTail - 1)

AcknowledgedPrefixWasQuorumCovered ==
    \A position \in 0..(knownTail - 1) : CoveredByQuorum(position)

TrimNeverPassesKnownTail ==
    \A server \in Servers : trimmed[server] <= knownTail

SealPreventsNewPositions ==
    sealBoundary = NoPosition \/ nextPosition = sealBoundary

PendingIsTheLatestAssignedPosition ==
    pending = NoPosition \/ pending = nextPosition - 1

=============================================================================
