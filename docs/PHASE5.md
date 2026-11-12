# Phase 5 — Experimental Validity

Connectivity-aware traffic (optional), fair multi-protocol workloads, Student-t CIs, OLSR overhead measurement.

## Key results (synthetic Walker N=50, connectivity-aware, seeds 42–43)

| Protocol | Delivery | Control |
|----------|----------|--------|
| predictive (oracle) | 1.00 | 0 |
| practical_predictive | 1.00 | 572 |
| aodv | 1.00 | ~700–1200 |
| olsr | 0.95 | ~82000 |

Workload fingerprints match across protocols within each (n, seed) cell.

OLSR overhead is protocol/tick behaviour on dense graphs — not artificially suppressed.

See full methodology in the local `docs/PHASE5.md` and modules `connectivity.py`, `experiments/phase5_matrix.py`.

**Tests:** 67 passed. Synthetic ≠ operational Starlink.
