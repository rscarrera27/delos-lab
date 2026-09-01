# Formal models

## VirtualLog reconfiguration

`VirtualLogReconfiguration.tla` models clients racing to extend, modify, and truncate a
LogChain while prefix trim advances. It checks:

- half-open segment boundaries have no gap or overlap;
- chain versions advance exactly once per successful CAS;
- opaque configuration count tracks segment count;
- `reconfigModify` changes only a sealed configuration epoch;
- `reconfigTruncate` removes only a fully trimmed leading sealed segment;
- stale prepared candidates cannot overwrite a newer chain.

With the checked configuration, TLC 2026.08.21.155922 explored 5,953,727 generated and
904,020 distinct states, with zero invariant violations.

## NativeLoglet

`NativeLoglet.tla` models a single sequencer retaining one pending position across
retries, per-LogServer storage and seal bits, quorum acknowledgement, partial/zombie
appends, sealed repair, `knownTail`, and monotonic prefix trim. It checks:

- acknowledged positions form a dense prefix;
- every acknowledged position remains quorum-accounted for by a copy or safe trim;
- trim never passes `knownTail`;
- a quorum seal prevents assignment of new positions;
- a pending append retains the latest assigned position.

With the checked configuration, TLC 2026.08.21.155922 explored 446,701 generated and
28,448 distinct states, with zero invariant violations.

```bash
java -cp /path/to/tla2tools.jar tlc2.TLC \
  -config formal/VirtualLogReconfiguration.cfg \
  formal/VirtualLogReconfiguration.tla

java -cp /path/to/tla2tools.jar tlc2.TLC \
  -config formal/NativeLoglet.cfg \
  formal/NativeLoglet.tla
```

These are finite models of the stated contracts, not proofs of the Python code. The
Paxos MetaStore is covered by unit, property, persistence, and process integration tests;
it does not have a TLA+ model in this repository. Database snapshot transfer and the
transition from one NativeLoglet storage-member set to the next are covered by unit and
subprocess integration tests, but are not yet part of these finite models.
