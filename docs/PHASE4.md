# Phase 4 — Large-Scale Validation & Profiling

**Hardware (measured):** Linux x86_64, Python 3.12.3, 2 CPUs, 1.29 GB RAM  
**Tests:** 59 passed

## Data classes

- **sample TLE** — repo sample; not full Starlink
- **synthetic Walker** — `constellation.generate_walker_tles` for scaling only

## Topology scale (synthetic Walker, max_range=3000 km, 5 epochs)

| N | spatial topo_s | links | candidates | RSS MB |
|--:|---------------:|------:|-----------:|-------:|
| 100 | 0.004 | 190 | 762 | 31 |
| 200 | 0.018 | 958 | 3066 | 33 |
| 400 | 0.066 | 3840 | 12480 | 35 |
| 800 | 0.285 | 15955 | 50214 | 45 |
| 1000 | 0.432 | 24728 | 78465 | 48 |
| 2000 | 1.873 | 100832 | 315063 | 118 |

Spatial vs brute matched for N≤200. Brute skipped for larger N.

## Full routing (N=50, seeds 42–44, max_range=5000)

| Protocol | Delivery mean | Control mean |
|----------|---------------|--------------|
| predictive | 1.000 | 0 |
| aodv | 1.000 | 740 |

## Commands

```bash
PYTHONPATH=. python -m experiments.scale_profile --sizes 100,200,400 --topology-only
PYTHONPATH=. python -m experiments.full_scale_sim --sizes 50 --seeds 42,43,44
```

See full methodology and limitations in repository docs/PHASE4.md after sync.
