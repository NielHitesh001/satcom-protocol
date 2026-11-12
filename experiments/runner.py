"""
experiments/runner.py – Multi-seed, multi-protocol experiment harness.

Usage (from repo root):
  PYTHONPATH=. python -m experiments.runner --scenario scenarios/example.yaml \\
      --protocols predictive,aodv,olsr --seeds 42,43,44 --out outputs/exp1
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, List

import yaml

from engine import run_scenario

# Two-sided 95% Student-t critical values for degrees of freedom 1–30.
_T975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
    7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
    13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
    18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
    23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052,
    28: 2.048, 29: 2.045, 30: 2.042,
}



def _mean_std(xs: List[float]) -> Dict[str, Any]:
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not xs:
        return {"mean": None, "std": None, "ci95_half_width": None, "n": 0}
    if len(xs) == 1:
        return {"mean": xs[0], "std": 0.0, "ci95_half_width": None, "n": 1}
    std = statistics.stdev(xs)
    n = len(xs)
    t_critical = _T975.get(n - 1, 1.960)
    return {
        "mean": statistics.mean(xs),
        "std": std,
        "ci95_half_width": t_critical * std / math.sqrt(n),
        "n": n,
    }


def run_matrix(
    scenario_path: Path,
    protocols: List[str],
    seeds: List[int],
    out_dir: Path,
    max_sats: int | None = None,
    duration_s: float | None = None,
) -> List[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = yaml.safe_load(scenario_path.read_text())
    rows: List[dict] = []

    for proto in protocols:
        for seed in seeds:
            data = dict(base)
            data["protocol"] = proto
            data["seed"] = seed
            data["name"] = f"{base.get('name', 'exp')}_{proto}_{seed}"
            if max_sats is not None:
                data["max_sats"] = max_sats
            if duration_s is not None:
                data["duration_s"] = duration_s
            tle = Path(data["tle_path"])
            if not tle.is_absolute():
                data["tle_path"] = str((scenario_path.parent / tle).resolve())
            scn = out_dir / f"scn_{proto}_{seed}.yaml"
            scn.write_text(yaml.dump(data))
            metrics = run_scenario(scn, output_dir=out_dir)
            s = metrics.summary_dict()
            rows.append(s)
            print(
                f"{proto:12s} seed={seed} del_ratio={s['delivery_ratio']:.3f} "
                f"lat={s['mean_latency_s']} ctrl={s['control_sent']}"
            )

    csv_path = out_dir / "all_runs.csv"
    if rows:
        keys = list(rows[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)

    by_proto: Dict[str, List[dict]] = {}
    for r in rows:
        by_proto.setdefault(r["protocol"], []).append(r)

    summary = {}
    for proto, runs in by_proto.items():
        summary[proto] = {
            "delivery_ratio": _mean_std([r["delivery_ratio"] for r in runs]),
            "mean_latency_s": _mean_std(
                [r["mean_latency_s"] for r in runs if r["mean_latency_s"] is not None]
            ),
            "control_sent": _mean_std([float(r["control_sent"]) for r in runs]),
            "n_seeds": len(runs),
        }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="SATCOM protocol experiment runner")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--protocols", default="predictive,aodv,olsr")
    ap.add_argument("--seeds", default="42,43,44")
    ap.add_argument("--out", default="outputs/experiments")
    ap.add_argument("--max-sats", type=int, default=None)
    ap.add_argument("--duration", type=float, default=None)
    args = ap.parse_args()

    protocols = [p.strip() for p in args.protocols.split(",") if p.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    run_matrix(
        Path(args.scenario),
        protocols,
        seeds,
        Path(args.out),
        max_sats=args.max_sats,
        duration_s=args.duration,
    )


if __name__ == "__main__":
    main()
