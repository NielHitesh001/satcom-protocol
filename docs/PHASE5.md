# Phase 5 — Experimental Validity, Connectivity-Aware Traffic & Protocol Evaluation

**Date:** 2026-11-12  
**Hardware:** 2 CPUs, 1.29 GB RAM, Python 3.12.3  
**Tests:** 67 passed  

## 1. Objectives

Improve **scientific reliability** of protocol comparisons:

1. Connectivity-aware traffic endpoints (optional; fixed mode unchanged)
2. Fair multi-protocol workloads (identical inputs per cell)
3. Documented metrics and residual handling
4. Student-t confidence intervals with honest flags
5. Measured OLSR control overhead and AODV residual/RERR behaviour

**Not goals:** larger N than Phase 4, GUI, RFC claims, fabricated results.

---

## 2. Connectivity-aware traffic

**Module:** `connectivity.py`

| Mode | Behaviour |
|------|-----------|
| `fixed` | Explicit src/dst (legacy). Optional post-hoc component annotation. |
| `connectivity_aware` | Build topology at injection time; pick distinct endpoints from a component with size ≥ `min_component_size` (weighted by size). |

**Metadata recorded:** source, destination, component_size, n_components, mode, injection_time_s, notes.

**Does not guarantee delivery.** Topology can change after injection; RERR, queues, and residuals still apply.

**Default scenarios unchanged** — connectivity-aware is opt-in via `experiments.phase5_matrix --traffic-mode connectivity_aware`.

---

## 3. Fair protocol comparison

`experiments/phase5_matrix.py`:

- For each `(n_sats, seed, traffic_mode)` cell, endpoints are **frozen once**
- Every protocol reuses that `EndpointChoice`
- `workload_fp` = SHA-256 of scenario JSON excluding `protocol`/`name`
- Fingerprints must match across protocols in the same cell (warnings if not)

---

## 4. Metric definitions

| Metric | Definition | Level | Undefined |
|--------|------------|-------|-----------|
| Delivery ratio | delivered / sent | run / flow | 0 if sent=0 |
| Drop ratio | dropped / sent | run / flow | residual **excluded** |
| Residual ratio | residual / sent | run | — |
| Mean/median/p95/p99 latency | on **delivered** packets only | run | JSON `null` if none |
| Control messages | protocol `_control_sent` | run | — |
| Control bytes | only if modeled (`control_bytes_sent`; often 0) | run | not invented |
| State size | `state_size(node)` peak | run | approximate |
| Discovery time | discovery_end − discovery_start at origin | AODV | null if none |
| Wall / RSS | process measurement | run | optional |

**Conservation:** `sent = delivered + dropped + residual`.

---

## 5. Statistical methodology

`experiments/stats_summary.py --method student_t|normal`

- **student_t (default):** \(\bar{x} \pm t_{n-1,0.975}\, s/\sqrt{n}\) with tabulated t  
- **normal:** z=1.96 approximation, flagged for small n  
- **Flags:** `no_samples`, `insufficient_sample_for_ci`, `zero_variance_degenerate_ci`  

Degenerate CIs (zero variance) are **not** evidence of precision. n=2 yields very wide t intervals (t_crit=12.706).

---

## 6. Measured matrix (synthetic Walker, connectivity-aware)

**Settings:** N=50, max_range=5000 km, duration=60 s, rate=0.5 pps, seeds 42–43, component size observed = 50.

| Protocol | Seed | Delivery | Residual | Control | workload_fp |
|----------|-----:|---------:|---------:|--------:|-------------|
| predictive (oracle) | 42 | 1.00 | 0 | 0 | 634b0573… |
| practical_predictive | 42 | 1.00 | 0 | 572 | same |
| aodv | 42 | 1.00 | 0 | 1211 | same |
| olsr | 42 | 0.95 | 0 | **81880** | same |
| predictive | 43 | 1.00 | 0 | 0 | 255432c9… |
| practical_predictive | 43 | 1.00 | 0 | 572 | same |
| aodv | 43 | 1.00 | 0 | 692 | same |
| olsr | 43 | 0.95 | 0 | **81880** | same |

Oracle remains an **optimistic benchmark**, not a deployable protocol.

---

## 7. OLSR control overhead investigation

**Probe (N=50, seed=42, connectivity-aware):** ~82 451 control messages in 60 s  
≈ **1374 msg/s** ≈ **1649 msg/sat** over the run.

**Sources (implementation behaviour, not a bug in the counter):**

1. HELLO every `hello_interval_s` (default 2 s) to all neighbors — cost scales with degree  
2. TC every `tc_interval_s` (default 5 s)  
3. TC retransmit by MPR selectors with TTL  
4. Tick density `min(time_step, hello_interval)` can schedule multiple ticks per topology epoch  

No artificial suppression applied. Baseline OLSR preserved. Any future optimization needs before/after measurements and regression tests.

---

## 8. AODV route recovery / residual

Probe (same workload): delivery 1.0, residual 0, control 1211, discoveries and RERR path exercised in unit tests.

Residuals appear when routes break and DATA remains buffered past sim end — counted as **residual**, not drop (Phase 2/5 accounting).

---

## 9. Reproduction

```bash
PYTHONPATH=. pytest tests/ -q   # 67 tests

PYTHONPATH=. python -m experiments.phase5_matrix \
  --sizes 50 --seeds 42,43 --traffic-mode connectivity_aware \
  --protocols predictive,practical_predictive,aodv,olsr \
  --max-range 5000 --duration 60 --out outputs/phase5_matrix

PYTHONPATH=. python -m experiments.stats_summary \
  outputs/phase5_matrix/matrix_results.csv --method student_t

PYTHONPATH=. python -m experiments.protocol_probe --n 50 --seed 42
```

---

## 10. Limitations

- Synthetic Walker ≠ operational Starlink  
- n=2 seeds → wide Student-t intervals; do not rank protocols from this alone  
- OLSR overhead is high under current timers on dense graphs  
- Connectivity at injection does not freeze the path for the flow lifetime  
- Control **bytes** not fully attributed for all message types  

## 11. Changed / new files

- `connectivity.py`  
- `experiments/phase5_matrix.py`, `protocol_probe.py`, `stats_summary.py` (t-CI)  
- `tests/test_phase5.py`  
- `docs/PHASE5.md`  
