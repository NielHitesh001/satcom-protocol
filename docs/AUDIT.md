# Repository Audit — satcom-protocol v0.1

**Date:** 2026-11-12  
**Baseline:** 15 pytest tests pass.

## Critical issues addressed in this cycle

- C1: Engine was time-stepped with zero delay → chronological event queue + real prop/serialization delay
- C2: Capacity unused → per-link busy time + queue limit
- C3: Packet fates collapsed → delivered / dropped / residual
- C5: Oracle only → PracticalPredictiveProtocol added
- C8: Metrics gaps → percentiles, loss ratio, null for missing

## Remaining limitations

- Topology still O(N²) per snapshot (spatial index planned)
- AODV intermediate RREP / full RERR still simplified
- OLSR tick density vs topology step still approximate
- SGP4+GMST is first-order (no polar motion)
- No PHY-layer model

See full audit history in repository commits and README.
