-------------------- MODULE VirtualLogReconfiguration --------------------
EXTENDS Integers, Sequences

CONSTANTS Clients, MaxLocalTail, MaxReconfigurations

NoCandidate == -1

VARIABLES firstStart, closedStops, activeStart, configEpochs, trimPoint,
          version, casCount, sealed, preparedVersion, preparedStop

vars == <<firstStart, closedStops, activeStart, configEpochs, trimPoint,
          version, casCount, sealed, preparedVersion, preparedStop>>

Init ==
    /\ firstStart = 0
    /\ closedStops = <<>>
    /\ activeStart = 0
    /\ configEpochs = <<0>>
    /\ trimPoint = 0
    /\ version = 1
    /\ casCount = 0
    /\ sealed = FALSE
    /\ preparedVersion = [client \in Clients |-> NoCandidate]
    /\ preparedStop = [client \in Clients |-> 0]

\* seal and checkTail are idempotent. Competing clients can observe different
\* later sealed tails because repair may make zombie appends durable.
Prepare(client, localTail) ==
    /\ casCount < MaxReconfigurations
    /\ localTail \in 0..MaxLocalTail
    /\ sealed' = TRUE
    /\ preparedVersion' = [preparedVersion EXCEPT ![client] = version]
    /\ preparedStop' = [preparedStop EXCEPT ![client] = activeStart + localTail]
    /\ UNCHANGED <<firstStart, closedStops, activeStart, configEpochs,
                    trimPoint, version, casCount>>

\* reconfigExtend closes the active half-open range and appends a fresh config.
Install(client) ==
    /\ sealed
    /\ preparedVersion[client] = version
    /\ LET virtualStop == preparedStop[client]
       IN  /\ closedStops' = Append(closedStops, virtualStop)
           /\ activeStart' = virtualStop
    /\ configEpochs' = Append(configEpochs, 0)
    /\ version' = version + 1
    /\ casCount' = casCount + 1
    /\ sealed' = FALSE
    /\ preparedVersion' = [preparedVersion EXCEPT ![client] = NoCandidate]
    /\ UNCHANGED <<firstStart, trimPoint, preparedStop>>

\* prefixTrim is monotonic. It supplies the evidence required by Truncate.
AdvanceTrim(newTrim) ==
    /\ newTrim \in trimPoint..activeStart
    /\ trimPoint' = newTrim
    /\ UNCHANGED <<firstStart, closedStops, activeStart, configEpochs,
                    version, casCount, sealed, preparedVersion, preparedStop>>

\* reconfigTruncate removes only a fully trimmed leading sealed segment.
Truncate ==
    /\ casCount < MaxReconfigurations
    /\ Len(closedStops) > 0
    /\ trimPoint >= Head(closedStops)
    /\ firstStart' = Head(closedStops)
    /\ closedStops' = Tail(closedStops)
    /\ configEpochs' = Tail(configEpochs)
    /\ version' = version + 1
    /\ casCount' = casCount + 1
    /\ UNCHANGED <<activeStart, trimPoint, sealed,
                    preparedVersion, preparedStop>>

\* reconfigModify changes opaque configuration only for a sealed segment.
Modify(index) ==
    /\ casCount < MaxReconfigurations
    /\ index \in 1..Len(closedStops)
    /\ configEpochs' = [configEpochs EXCEPT ![index] = @ + 1]
    /\ version' = version + 1
    /\ casCount' = casCount + 1
    /\ UNCHANGED <<firstStart, closedStops, activeStart, trimPoint, sealed,
                    preparedVersion, preparedStop>>

\* A losing installer observes any newer CAS, including modify or truncate.
DiscardStale(client) ==
    /\ preparedVersion[client] # NoCandidate
    /\ preparedVersion[client] # version
    /\ preparedVersion' = [preparedVersion EXCEPT ![client] = NoCandidate]
    /\ UNCHANGED <<firstStart, closedStops, activeStart, configEpochs,
                    trimPoint, version, casCount, sealed, preparedStop>>

Next ==
    \/ \E client \in Clients, localTail \in 0..MaxLocalTail:
        Prepare(client, localTail)
    \/ \E client \in Clients: Install(client)
    \/ \E newTrim \in trimPoint..activeStart: AdvanceTrim(newTrim)
    \/ Truncate
    \/ \E index \in 1..Len(closedStops): Modify(index)
    \/ \E client \in Clients: DiscardStale(client)

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ firstStart \in Nat
    /\ closedStops \in Seq(Nat)
    /\ activeStart \in Nat
    /\ configEpochs \in Seq(Nat)
    /\ trimPoint \in Nat
    /\ version \in Nat
    /\ casCount \in Nat
    /\ sealed \in BOOLEAN
    /\ preparedVersion \in [Clients -> Int]
    /\ preparedStop \in [Clients -> Nat]

VersionTracksCAS == version = casCount + 1

ConfigurationTracksSegments == Len(configEpochs) = Len(closedStops) + 1

HalfOpenBoundaryHasNoGap ==
    /\ (Len(closedStops) = 0 => activeStart = firstStart)
    /\ (Len(closedStops) > 0 => activeStart = closedStops[Len(closedStops)])

ClosedStopsAreMonotonic ==
    /\ (Len(closedStops) = 0 \/ firstStart <= closedStops[1])
    /\ (Len(closedStops) <= 1 \/
        \A index \in 1..(Len(closedStops) - 1):
            closedStops[index] <= closedStops[index + 1])

TrimPointIsRetainedBoundary == firstStart <= trimPoint /\ trimPoint <= activeStart

CurrentCandidatesCannotMoveBackward ==
    \A client \in Clients:
        preparedVersion[client] = version => preparedStop[client] >= activeStart

CandidatesAreNeverFromTheFuture ==
    \A client \in Clients:
        preparedVersion[client] = NoCandidate \/ preparedVersion[client] <= version

=============================================================================
