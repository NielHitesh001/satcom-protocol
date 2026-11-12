# Phase 3 — Performance & Scalability

## Spatial index for sat–sat topology

`links.compute_snapshot` builds candidate pairs with a 3-D hash grid:

- Cell size = `max_range_km`
- Each satellite is matched only against others in its cell and 26 neighbours
- **No false negatives** for Euclidean range ≤ max_range
- Auto-enabled when N > 20; override with `use_spatial_index=True/False`

## Benchmark (max_range=3000 km)

| N | Brute (ms) | Spatial (ms) | Speedup |
|--:|-----------:|-------------:|--------:|
| 20 | 1.4 | 0.5 | 2.6× |
| 50 | 8.3 | 1.8 | 4.7× |
| 100 | 36.9 | 5.2 | 7.1× |
| 200 | 144.8 | 20.6 | 7.0× |
| 400 | 578.8 | 86.9 | 6.7× |

Run: `PYTHONPATH=. python scripts/bench_topology.py`

## Correctness

Spatial and brute-force modes produce identical edge sets and distances.
