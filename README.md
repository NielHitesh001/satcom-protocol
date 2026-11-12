# Satellite Ad Hoc Network Simulator (v0.1)

Discrete-event simulator for ad-hoc routing over a LEO constellation, driven by real TLE orbital data (Starlink model).

## Goals (Stage 1)

- Evaluate whether AODV-style, OLSR-style and predictive routing work on a fast-moving LEO mesh.
- Measure delivery ratio, latency, control overhead and routing-state memory.
- Produce reproducible runs from TLE snapshots + YAML scenarios.

## Quick start

```bash
pip install -r requirements.txt
python run_example.py
# or
pytest tests/ -q
```

Programmatically:

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
  scripts/           fetch_tles.py (live CelesTrak pull)
  tests/             Geometry, routing, and engine tests
  run_example.py     Run all three protocols on the example scenario
```

## Routing protocols

All three implement the same interface (`on_link_change`, `on_packet`, `next_hop`, `state_size`).

| Protocol | Style | What it does |
|----------|--------|--------------|
| **AODV** (simplified) | On-demand | RREQ flooding, RREP, DATA buffering during discovery, sequence numbers, route expiry. Not a full RFC 3561 implementation. |
| **OLSR** (simplified) | Proactive | Periodic HELLO + TC, greedy MPR (2-hop cover), TC retransmit only by MPR selectors. Not a full RFC 3626 implementation. |
| **Predictive** | Oracle baseline | Precomputes shortest-path next hops from the known future topology snapshots. Zero control traffic. |

### Predictive protocol (design note)

Orbital motion is deterministic given a TLE snapshot. The simulator therefore knows the full time-varying topology in advance. The predictive protocol:

1. Receives every topology snapshot before the run starts.
2. Runs all-pairs shortest paths (Dijkstra on hop count) on each snapshot.
3. At query time, returns the next hop from the most recent snapshot at or before the current simulation time.

**Assumptions**
- Perfect future knowledge of the link graph (no prediction error).
- Instant, free distribution of the schedule to every node (no uplink cost modelled).
- Routes are recomputed only at snapshot boundaries (the scenario `time_step_s`).

**Role in the study**  
It is a *lower-bound reference*, not a candidate on-board algorithm. It answers: “If nodes somehow knew the future topology, how good could delivery ratio and latency be?” AODV and OLSR are then compared against that bound under the same traffic, seed, and topology.

## Example results

Scenario: `scenarios/example.yaml` (15 sats, 180 s, seed 42, sat-to-sat flows on a connected component).  
**These numbers are relative to the simplified protocol implementations above.**

| Protocol   | Sent | Delivered | Delivery ratio | Mean latency (s) | Control msgs | Mean discovery (s) |
|------------|-----:|----------:|---------------:|-----------------:|-------------:|-------------------:|
| predictive |   39 |        39 |          1.000 |             6.3  |            0 |                —   |
| aodv       |   39 |        39 |          1.000 |             6.3  |          178 |               0.0  |
| olsr       |   39 |        35 |          0.897 |             6.4  |         4818 |                —   |

Predictive and AODV deliver fully. OLSR is slightly below 100% because MPR selection needs a HELLO exchange before TC retransmits; early packets can miss routes. OLSR control is still higher than AODV but ~2× lower than the pre-MPR flood. Discovery latency is 0 under the discrete same-step model (RREQ/RREP finish within one topology snapshot).

### Live TLE snapshots

```bash
python scripts/fetch_tles.py --max 100 --out data/starlink_live.tle
```

Point `tle_path` in the scenario YAML at that file. Requires network access to CelesTrak.

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

- AODV and OLSR are simplified. Results should be read as relative to these baselines, not as RFC-faithful protocol comparisons.
- SGP4 returns TEME; positions are rotated into ECEF with a GMST (IAU/Vallado) approximation so ground-site elevation and LOS are consistent. Polar motion and nutation are omitted.
- Full Starlink constellation (thousands of sats) is expensive; start with `max_sats`.
- No physical-layer coding or attitude dynamics.
- The predictive protocol is an oracle (see above), not an on-board algorithm.

## Validation

```bash
pytest tests/ -q
```

| Suite | What it covers |
|-------|----------------|
| `test_geometry` | LOS, elevation, coordinate conversion, TEME→ECEF / GMST |
| `test_routing`  | Static graph with hand-computed paths (predictive, OLSR, AODV route install / invalidation) |
| `test_engine`   | Snapshot time ordering, reproducibility under fixed seed, packet conservation, output files |
