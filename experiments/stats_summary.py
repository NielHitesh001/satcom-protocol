"""
Aggregate experiment CSVs with documented confidence intervals.

Methods:
  - student_t (default): mean ± t_{n-1, 1-α/2} * s/√n  (unknown variance, small n)
  - normal: mean ± z * s/√n  (explicitly labeled large-sample approximation)

Zero variance, n<2, and missing values are reported transparently — never dressed
up as strong evidence.

Usage:
  PYTHONPATH=. python -m experiments.stats_summary results.csv --method student_t
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional


# Critical values for two-sided 95% CI, Student's t, df = n-1
# df: t
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    15: 2.131, 20: 2.086, 30: 2.042, 40: 2.021, 60: 2.000,
    120: 1.980,
}


def _t_crit_95(df: int) -> float:
    if df < 1:
        return float("nan")
    if df in _T95:
        return _T95[df]
    # nearest lower tabulated or asymptotic
    keys = sorted(_T95)
    for k in reversed(keys):
        if df >= k:
            return _T95[k]
    return 1.96


def summarize_values(
    xs: List[float],
    method: str = "student_t",
    alpha: float = 0.05,
) -> Dict[str, Optional[float]]:
    """
    Return mean, std, n, ci method, and interval.

    For n < 2: CI is null; note insufficient_sample.
    For std == 0 and n >= 2: CI collapses to [mean, mean]; flagged zero_variance.
    """
    clean = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    n = len(clean)
    out: Dict[str, Optional[float]] = {
        "mean": None,
        "std": None,
        "n": n,
        "ci95_lo": None,
        "ci95_hi": None,
        "method": method,
        "flag": None,
    }
    if n == 0:
        out["flag"] = "no_samples"
        return out
    mean = statistics.mean(clean)
    out["mean"] = mean
    if n == 1:
        out["std"] = 0.0
        out["flag"] = "insufficient_sample_for_ci"
        return out
    std = statistics.stdev(clean)
    out["std"] = std
    if std == 0.0:
        out["ci95_lo"] = mean
        out["ci95_hi"] = mean
        out["flag"] = "zero_variance_degenerate_ci"
        return out

    se = std / math.sqrt(n)
    if method == "student_t":
        if abs(alpha - 0.05) > 1e-9:
            out["flag"] = "only_95pct_t_table_supported"
        tcrit = _t_crit_95(n - 1)
        half = tcrit * se
        out["ci95_lo"] = mean - half
        out["ci95_hi"] = mean + half
        out["t_crit"] = tcrit
    elif method == "normal":
        z = 1.96 if abs(alpha - 0.05) < 1e-9 else 1.96
        half = z * se
        out["ci95_lo"] = mean - half
        out["ci95_hi"] = mean + half
        out["z"] = z
        out["flag"] = "normal_approx_not_for_small_n"
    else:
        out["flag"] = f"unknown_method_{method}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--group-by", default="protocol,n_sats")
    ap.add_argument(
        "--metrics",
        default="delivery_ratio,mean_latency_s,control_sent,wall_s,packets_residual",
    )
    ap.add_argument("--method", choices=("student_t", "normal"), default="student_t")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    path = Path(args.csv_path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    keys = [k.strip() for k in args.group_by.split(",")]
    metrics = [m.strip() for m in args.metrics.split(",")]

    groups: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        if r.get("error"):
            continue
        gk = tuple(r.get(k, "") for k in keys)
        groups[str(gk)].append(r)

    summary = {
        "_meta": {
            "method_default": args.method,
            "note": (
                "Student-t CI uses tabulated 95% critical values. "
                "Degenerate/zero-variance intervals are flagged, not evidence of precision."
            ),
        }
    }
    for gk, rs in groups.items():
        entry: dict = {"group": gk, "n_runs": len(rs)}
        for m in metrics:
            vals = []
            for r in rs:
                v = r.get(m)
                if v is None or v == "":
                    continue
                try:
                    vals.append(float(v))
                except ValueError:
                    continue
            entry[m] = summarize_values(vals, method=args.method)
        summary[gk] = entry

    text = json.dumps(summary, indent=2)
    print(text)
    out = Path(args.out) if args.out else path.with_suffix(".stats.json")
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
