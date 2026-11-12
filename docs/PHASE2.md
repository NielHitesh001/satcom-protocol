# Phase 2 — Routing Improvements

## AODV
- Intermediate RREP when a node has a fresh route to the destination
- RERR on link break with precursor notification and limited propagation
- Expired routes removed on access
- DATA still buffered at end-of-sim counted as **residual** (not drop)

## OLSR
- Topology entries stored as `(neighbor_set, timestamp)`
- Entries older than `hold_time_s` expire on tick / recompute
- Neighbor soft-state expiry via `neighbor_time`
- `seen_tc` bounded to prevent unbounded growth

## Practical predictive (non-oracle)
- Residual link lifetime from range-rate between topology snapshots
- Edge weight = 1 + distance penalty + lifetime penalty
- Links with residual lifetime < 0.5 s are skipped in Dijkstra
- Horizon (`horizon_s`) controls soft preference for stable links

## Example results (seed 42, 15 sats, 180 s)

| Protocol | Delivery | Residual | Control |
|----------|----------|----------|--------|
| predictive (oracle) | 1.000 | 0 | 0 |
| practical_predictive | 1.000 | 0 | 288 |
| aodv | 0.692 | 12 | 174 |
| olsr | 0.974 | 0 | 15016 |

AODV residual packets are those still buffered after routes were invalidated by topology changes (RERR path). Conservation `sent = delivered + dropped + residual` holds.

## Tests
`tests/test_routing_phase2.py` — intermediate RREP, RERR, OLSR expiry, lifetime weighting.
