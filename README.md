# Satellite Ad Hoc Network Simulator

Research-oriented discrete-event simulator for **ad hoc routing over a LEO mesh**, driven by TLE/SGP4 orbital data.

**Not flight software.** Protocols are simplified relative to RFCs.

## Quick start

```bash
pip install -r requirements.txt
PYTHONPATH=. pytest tests/ -q
PYTHONPATH=. python run_example.py
```

```bash
PYTHONPATH=. python -m experiments.runner --scenario scenarios/example.yaml \\
  --protocols predictive,practical_predictive,aodv,olsr --seeds 42,43,44 --out outputs/exp1
PYTHONPATH=. python scripts/scale_experiment.py --sizes 10,15,20,30
PYTHONPATH=. python scripts/bench_topology.py
```

## Architecture

| Module | Role |
|--------|------|
| `engine.py` | Chronological event queue (topology / tick / inject / arrival) |
| `orbit.py` | TLE + SGP4 + TEME→ECEF (GMST) |
| `links.py` | LOS/range/elevation; 3-D spatial hash; O(1) edge_index |
| `routing/` | AODV, OLSR, practical predictive, oracle |
| `metrics.py` | Delivery, residual, p50/p95/p99 (null if undefined) |
| `docs/` | AUDIT, ENGINE, PHASE2, PHASE3 |

Packet conservation: `sent = delivered + dropped + residual`.

## Example results (15 sats, 180 s, seed 42)

| Protocol | Delivery | Residual | Latency | Control |
|----------|----------|----------|---------|--------|
| predictive (oracle) | 1.000 | 0 | ~0.06 s | 0 |
| practical_predictive | 1.000 | 0 | ~0.06 s | ~288 |
| aodv | ~0.69 | ~12 | ~0.06 s | ~174 |
| olsr | ~0.97 | 0 | ~0.06 s | ~15k |

## Topology speedup (spatial vs brute, N=100–400)

~7× faster with identical edge sets. See `docs/PHASE3.md`.

## Assumptions

SGP4+GMST for topology trends (not cm geodesy). Simplified protocols. Capacity = bit-rate FIFO. Does not contact live Starlink service.
