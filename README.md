# Satellite Ad Hoc Network Simulator (v0.1)

Discrete-event simulator for ad-hoc routing over a LEO constellation, driven by real TLE orbital data (Starlink model).

## Goals (Stage 1)

- Evaluate whether AODV-style, OLSR-style and predictive routing work on a fast-moving LEO mesh.
- Measure delivery ratio, latency, control overhead and routing-state memory.
- Produce reproducible runs from TLE snapshots + YAML scenarios.

## Quick start

```bash
# From the repository root
pip install -r requirements.txt
python -m run_example
# or
python run_example.py
```

Or programmatically:

```python
from engine import run_scenario
metrics = run_scenario("scenarios/example.yaml", output_dir="outputs")
print(metrics.summary_dict())
```

## Layout

```
.
  config.py          Scenario loading
  orbit.py           TLE + SGP4 propagation (TEME→ECEF via GMST)
  links.py           Visibility / range / elevation graph
  routing/           AODV, OLSR, predictive
  traffic.py         Flow generation
  engine.py          Discrete-event core
  metrics.py         CSV + manifest export
  analysis.py        Plots
  scenarios/         Example YAML
  data/              Sample TLEs
  tests/             Geometry unit tests
  run_example.py     Run all three protocols on the example scenario
```

## Scenario file

See `scenarios/example.yaml`. Key fields:

- `start_time_utc`, `duration_s`, `time_step_s`
- `tle_path`, `tle_snapshot_id`, `max_sats`
- `protocol`: `aodv` | `olsr` | `predictive`
- `ground_sites`, `link`, `traffic`, `seed`

## Outputs

Each run writes:

- `*_flows.csv` – per-flow delivery, latency, hop count
- `*_manifest.json` – run summary (seed, protocol, control overhead, …)

## Notes / limitations (v0.1)

- AODV and OLSR are simplified (not full RFC implementations). Results should be read as relative to these simplified baselines, not as RFC-faithful protocol comparisons.
- SGP4 returns TEME; positions are rotated into ECEF with a GMST (IAU/Vallado) approximation so ground-site elevation and LOS are consistent. Polar motion and nutation are omitted.
- Full Starlink constellation (thousands of sats) is expensive; start with `max_sats`.
- No physical-layer coding or attitude dynamics.
- The predictive protocol is an oracle that precomputes shortest paths from the known future topology snapshots. It is a lower-bound reference, not an on-board algorithm.

## Validation

```bash
pytest tests/ -q
```

Geometry tests cover LOS, elevation, coordinate conversion, and TEME→ECEF rotation. Routing and engine tests are still thin.
