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
| aodv | 1.000 | 0 | 179 |
| olsr | 0.974 | 0 | 15016 |

The earlier AODV row (delivery 0.692, residual 12, control 174) was not RERR behaviour. Reverse-route install kept a shorter entry when the origin sequence was newer, so the RREP after expiry never reached the source. Sequence-aware reverse routes deliver all 39 example packets. OLSR control is 15016 with a lexical MPR tie-break (it used to follow set hash order).

Conservation `sent = delivered + dropped + residual` holds.

## Tests
`tests/test_routing_phase2.py` — intermediate RREP, RERR, OLSR expiry, lifetime weighting.
