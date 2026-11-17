# Phase 7 — Goodput, hop metrics, and comparison hygiene

**Date:** 2026-11-18  
**Scope:** Paper-facing metrics, practical-predictive robustness, and a controlled comparison scenario.

Oracle predictive remains a non-deployable lower bound. All claims stay within the simplified-protocol label.

---

## 1. What changed

### Metrics (`metrics.py`, `engine.py`)

| Field | Meaning |
|-------|---------|
| `data_bytes_delivered` | Sum of application payload bytes that reached the destination |
| `goodput_bps` | `data_bytes_delivered * 8 / duration_s` (null/0 when undefined) |
| `mean_hops` | Already present; now consistently paired with goodput in summaries |

Delivery paths that count a packet as delivered (including same-node inject) now also increment `data_bytes_delivered`. Conservation is unchanged: `sent = delivered + dropped + residual`.

### Practical predictive (`routing/practical_predictive.py`)

- Range-rate used for residual lifetime is clamped to ±50 km/s so a single noisy snapshot cannot collapse lifetime to zero.
- Closing links keep a mild preference (`horizon * 1.5`) over opening links without treating them as immortal.

### Scenario

- `scenarios/walker_compare.yaml` — N≈30, 120 s, two flows, intended for multi-protocol runner comparisons. Still synthetic; prefer connectivity-aware endpoint selection from Phase 5 for fair ranking.

---

## 2. How to use

```bash
PYTHONPATH=. pytest tests/ -q

# Single-protocol example (existing)
PYTHONPATH=. python run_example.py

# Multi-seed comparison (existing runner)
PYTHONPATH=. python -m experiments.runner \
  --scenario scenarios/walker_compare.yaml \
  --protocols predictive,practical_predictive,aodv,olsr \
  --seeds 42,43,44 \
  --out outputs/phase7_compare
```

Summaries now include `goodput_bps` and `data_bytes_delivered` alongside delivery ratio and control cost.

---

## 3. Suggested follow-ups (not done here)

1. Wire `walker_compare` (or a generated Walker TLE file) into the fair matrix so endpoints are connectivity-aware by default.
2. Multi-seed tables with Student-t CIs for goodput and mean hops at N=30 and N=50.
3. Optional control priority / separate control capacity experiment (still simplified, not RFC MAC).
4. Document exact host, Python, and seed set used for any paper table.

---

## 4. Non-goals (unchanged)

- Full RFC 3561 / 3626 fidelity.
- Continuous orbital motion between topology steps.
- Claiming flight-software or live-network interaction.
