# Repository Audit — satcom-protocol v0.1

**Date:** 2026-11-12  
**Scope:** Full source review prior to research-grade improvements.  
**Baseline:** 15 pytest tests pass; example scenario runs for AODV / OLSR / predictive.

---

## 1. Module map

| Module | LOC | Role |
|--------|-----|------|
| `config.py` | 107 | YAML/JSON scenario → `Scenario` dataclass |
| `orbit.py` | 202 | TLE load, SGP4, TEME→ECEF (GMST), geodetic helpers |
| `links.py` | 133 | LOS, elevation, range snapshots (O(N²) sat–sat) |
| `traffic.py` | 50 | Deterministic packet generation from flow rates |
| `engine.py` | 283 | Time-stepped loop, in-flight list, protocol hooks |
| `metrics.py` | 103 | Per-flow counters, CSV + JSON manifest |
| `analysis.py` | 71 | Basic matplotlib plots |
| `routing/base.py` | 91 | `Packet`, `RoutingProtocol` ABC |
| `routing/aodv.py` | 265 | Simplified AODV (buffer + RREQ/RREP) |
| `routing/olsr.py` | 249 | Simplified OLSR (HELLO/TC + greedy MPR) |
| `routing/predictive.py` | 105 | Perfect-knowledge oracle (not deployable) |
| `tests/*` | ~350 | Geometry, static routing, engine smoke tests |

**Packet lifecycle (current):**

```
TrafficGenerator → inject at src
  → protocol.on_packet (route or buffer/discover)
  → engine._send → in_flight[(t, node, pkt)]
  → same-step delivery loop (delay forced to 0)
  → on_packet at next hop … until dst or drop
  → end-of-sim: remaining in_flight counted as dropped
```

**Orbital pipeline:**

```
TLE → Satrec → sgp4(jd,fr) TEME → teme_to_ecef(GMST) → ECEF km
  → compute_snapshot (LOS + range + elev) → TopologySnapshot
```

---

## 2. Critical correctness issues

### C1 — Engine is time-stepped, not discrete-event
- Advances only on `time_step_s` grid (topology epochs).
- Propagation delay is **forced to zero** so multi-hop control finishes in one step.
- No chronological priority queue; simultaneous events are ordered by list iteration only.
- **Impact:** Latency metrics understate path delay; control timing is unrealistic.

### C2 — Link capacity and queues unused
- `LinkEdge.capacity_mbps` is stored but never enforced.
- No transmission serialization delay (`size / bitrate`).
- No per-link queues, queue limits, or congestion drops.
- `loss_prob` in `LinkParams` is never applied.

### C3 — Packet fate categories collapsed
- Engine does not distinguish: delivered / network-drop / buffer-drop / expired / residual-at-end.
- Metrics treat “no observations” as 0.0 latency/jitter (misleading).

### C4 — Topology construction O(N²) every step
- All sat–sat pairs checked each snapshot; no spatial index.
- Scales poorly beyond tens of satellites.

### C5 — Predictive is only an oracle
- `PredictiveProtocol` has perfect future topology knowledge and zero control cost.
- Must not be compared as a deployable protocol; needs a separate practical predictor.

### C6 — AODV incompleteness
- No intermediate RREP; no RERR flooding on path break for buffered traffic.
- Route expiry is passive; no active keep-alive.
- Discovery latency reported as 0 under zero-delay same-step model.

### C7 — OLSR timing vs topology step
- HELLO/TC intervals (2s / 5s) vs typical `time_step_s` (15s) → at most one tick per topology epoch.
- MPR selector state depends on HELLO exchange; early DATA can miss routes (~10% in example).

### C8 — Metrics gaps
- Missing: median/p95/p99 latency, loss ratio, goodput, control bytes, hop-count distribution, queueing delay, residual packet counts.
- Zero used for “no data” (violates “never report missing as zero”).

### C9 — Orbital validation thin
- GMST TEME→ECEF is a valid first-order model; polar motion/nutation omitted (documented).
- No TLE age warning; SGP4 error codes only yield NaN positions.
- Sample TLE file is synthetic/small; ground links rare with current sample.

### C10 — Experiment framework absent
- No multi-seed / multi-size runner; no confidence intervals; results are single-run tables in README.

---

## 3. What works (preserve)

- Modular protocol interface (`RoutingProtocol`).
- TLE → SGP4 → GMST ECEF path is coherent for topology *trends*.
- AODV DATA buffering + RREQ/RREP flood delivers on connected graphs.
- OLSR MPR + TC duplicate suppression bounds control cost.
- Oracle predictive gives a true lower-bound reference.
- Geometry unit tests (LOS, elevation, TEME/ECEF).
- Seed reproducibility for traffic injection.
- Packet conservation test (sent = delivered + dropped after end-of-sim accounting).

---

## 4. Implementation plan (ordered)

| Phase | Focus | Priority |
|-------|--------|----------|
| **P0** | True event queue + real propagation delay + packet fate enum | Critical |
| **P0** | Apply `loss_prob`; serialization delay; optional simple link queue | Critical |
| **P1** | Metrics: percentiles, loss ratio, residual, NaN for missing | High |
| **P1** | Experiment runner (seeds × protocols × sizes) | High |
| **P2** | Practical predictive routing (horizon + schedule cost) | High |
| **P2** | AODV intermediate RREP + basic RERR | Medium |
| **P2** | OLSR topology expiry + tick independent of topology step | Medium |
| **P3** | Spatial hash for sat–sat links; vectorized propagation | Medium |
| **P3** | Expanded tests; docs; AUDIT residual limitations | Ongoing |

---

## 5. Non-goals for this improvement cycle

- Full RFC 3561 / 3626 compliance.
- PHY-layer modulation / coding.
- 3D visualization (architecture hooks only).
- Claiming “flight software” or physical realism beyond stated assumptions.

---

## 6. Assumptions that remain after fixes

- SGP4 + GMST ECEF is adequate for relative topology; not for cm-level geodesy.
- Capacity model is bit-rate only (no MAC contention beyond FIFO queue).
- Predictive *oracle* remains a benchmark only.
- Batch runs preferred over GUI.


## Phase 1 status (2026-11-12)

**Completed**
- Chronological event-queue engine validated (propagation, serialization, queues, loss, accounting, termination, ordering).
- Metrics: null latency, residual vs drop, percentiles documented.
- Test suite expanded: 46 tests passing.
- Docs: `docs/ENGINE.md` event model and metric definitions.

**Git synchronization**
- Local workspace remains source of truth until `engine.py` / `metrics.py` push succeeds.


## Phase 2 status (2026-11-12)

**Routing improvements**
- AODV: intermediate RREP, RERR with precursor notification and propagation, route expiry cleanup, buffer residual accounting at sim end.
- OLSR: topology and neighbor entry expiry via `hold_time_s`, seen_tc bound, recompute after expire.
- Practical predictive: range-rate residual lifetime estimation, horizon-based edge weighting, skip near-dead links.

**Validation**
- 52 tests passing (6 new Phase-2 routing tests).
- Packet conservation holds for all protocols including buffered AODV residual.

**Observed behaviour**
- AODV delivery on the example scenario is lower than the pre-RERR baseline (~0.69 vs 1.0) because link breaks now invalidate multi-hop routes and buffered packets remain residual. This is more accurate than silently keeping stale routes.


## Phase 3 status (2026-11-12)

**Scalability**
- Spatial hash grid for sat–sat candidate pairs (identical topology to brute force).
- Vectorized JD/GMST for the time grid in `propagate_positions`.
- Benchmark: ~7× faster topology at N=100–400 vs O(N²).
- 56 tests passing including spatial equivalence suite.


## Phase 4 status (2026-11-12)

- Synthetic Walker constellation generator (`constellation.py`) — labeled synthetic only.
- Scale profiling to N=2000 topology snapshots on 2-CPU / 1.3GB host.
- Full routing multi-seed at N=50 (predictive, AODV).
- 59 tests; see `docs/PHASE4.md` for measured tables and methodology.


## Phase 5 status (2026-11-12)

- Connectivity-aware endpoint selection (`connectivity.py`); fixed mode unchanged.
- Fair matrix runner with workload fingerprints across protocols.
- Student-t CIs with explicit zero-variance / small-n flags.
- OLSR overhead measured (~1.3k msg/s at N=50 dense synthetic); not artificially reduced.
- 67 tests; see `docs/PHASE5.md`.

## Phase 6 status (2026-11-12)

- Control PDU size model (`routing/control_sizes.py`); defaults match previous hardcoded sizes.
- Generation bytes vs scheduled airtime bytes vs non-airtime estimates are separate metrics.
- Queue policy unchanged (DATA only). OLSR not throttled.
- One-factor sensitivity harness. 73 tests. See `docs/PHASE6.md`.

