# Satellite Ad Hoc Network Simulator

Research-oriented discrete-event simulator for **ad hoc routing over a LEO mesh**, driven by TLE/SGP4 orbital data (Starlink-class model).

**Not flight software.** Protocols are simplified relative to RFCs and are labeled as such in results.

---

## Quick start

```bash
pip install -r requirements.txt
PYTHONPATH=. pytest tests/ -q
PYTHONPATH=. python run_example.py
```

```python
from engine import run_scenario
m = run_scenario("scenarios/example.yaml", output_dir="outputs")
print(m.summary_dict())
```

Multi-seed experiment:

```bash
PYTHONPATH=. python -m experiments.runner \
  --scenario scenarios/example.yaml \
  --protocols predictive,practical_predictive,aodv,olsr \
  --seeds 42,43,44 \
  --out outputs/exp1
```

Scale sweep:

```bash
PYTHONPATH=. python scripts/scale_experiment.py --sizes 10,15,20,30
PYTHONPATH=. python scripts/bench_topology.py
```

---

## Architecture

| Module | Role |
|--------|------|
| `engine.py` | Chronological `heapq` event queue (topology / tick / inject / arrival) |
| `orbit.py` | TLE load, SGP4, TEME→ECEF (GMST), vectorized time-grid GMST |
| `links.py` | LOS, elevation, range; **3-D spatial hash** for sat–sat pairs |
| `routing/` | AODV, OLSR, practical predictive, oracle predictive |
| `metrics.py` | Delivery, loss, residual, p50/p95/p99 latency (null if undefined) |
| `experiments/` | Multi-seed protocol comparison runner |
| `docs/` | AUDIT, ENGINE, PHASE2–PHASE6 |

**Event priority (same timestamp):** topology and `tx_done` (queue release), then tick → inject → arrival → end. Same kind breaks ties by insertion order.

**Packet fates:** `sent = delivered + dropped + residual`  
Residual = in-flight or still buffered at termination (not counted as a network drop).

**Delays:** `prop = distance/c`, `serialization = bits/capacity`; per-directed-link busy time + queue limit; optional Bernoulli `loss_prob`.

---

## Routing protocols

| Protocol | Role |
|----------|------|
| `predictive` / `oracle` | Perfect future topology — **benchmark only**, not deployable |
| `practical_predictive` | Live graph + range-rate residual lifetime weighting |
| `aodv` | On-demand RREQ/RREP, intermediate RREP, RERR, DATA buffer |
| `olsr` | HELLO/TC, greedy MPR, topology hold-time expiry |

None claim full RFC 3561 / 3626 fidelity.

---

## Example results

Scenario: `scenarios/example.yaml` (≤15 sats, 180 s, seed 42).  
Latencies include light-time + serialization (event-queue engine).

| Protocol | Delivery | Residual | Mean latency | Control |
|----------|----------|----------|--------------|---------|
| predictive (oracle) | 1.000 | 0 | ~0.060 s | 0 |
| practical_predictive | 1.000 | 0 | ~0.060 s | 288 |
| aodv | 1.000 | 0 | ~0.069 s | 179 |
| olsr | 0.974 | 0 | ~0.059 s | 15016 |

AODV delivers all 39 example packets. An earlier build reported delivery ~0.69 and residual ~12; that was not RERR. The reverse route ignored a newer origin sequence, so after the first route expired the returning RREP was discarded and DATA stayed buffered. RREP is forwarded toward the origin. OLSR's control count is stable across process hash seeds (MPR ties break by node name). One OLSR data packet is still dropped.

---

## Topology performance

Spatial hash vs brute force (synthetic LEO, max_range 3000 km):

| N | Brute | Spatial | Speedup |
|--:|------:|--------:|--------:|
| 100 | ~37 ms | ~5 ms | ~7× |
| 400 | ~579 ms | ~87 ms | ~7× |

---

## Assumptions & limits

- SGP4 + first-order GMST ECEF (no polar motion); adequate for topology **trends**, not cm geodesy.
- Capacity model is bit-rate FIFO per directed link; no full MAC contention.
- Topology evolves on the scenario `time_step_s` grid; packets use continuous time.
- Sample TLE set is small/synthetic — fetch live sets with `scripts/fetch_tles.py` when network allows.
- Starlink terms restrict product use of the live service; this simulator does not touch the live network.

---

## Documentation

| Doc | Content |
|-----|---------|
| `docs/AUDIT.md` | Full repository audit and phase history |
| `docs/ENGINE.md` | Event model, delays, metrics conventions |
| `docs/PHASE2.md` | Routing improvements |
| `docs/PHASE3.md` | Spatial index and benchmarks |
| `docs/PHASE4.md` | Large-constellation profiling |
| `docs/PHASE5.md` | Connectivity-aware traffic and fair comparison |
| `docs/PHASE6.md` | Control-byte accounting and sensitivity |

---

## Tests

```bash
PYTHONPATH=. pytest tests/ -q
```

Covers geometry (GMST/LOS), event delays, queue drops, TTL, loss, accounting, metrics edge cases, static routing, reverse-route sequence, intermediate RREP/RERR, OLSR expiry and MPR tie-break, spatial↔brute equivalence.
