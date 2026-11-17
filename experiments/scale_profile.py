"""
Large-scale constellation profiling framework.

Measures subsystem times for synthetic Walker constellations and optional
sample TLE. Does not fabricate numbers — only records measured values.

Usage:
  PYTHONPATH=. python -m experiments.scale_profile
  PYTHONPATH=. python -m experiments.scale_profile --sizes 10,50,100,200 --topology-only
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import psutil
except ImportError:
    psutil = None

from config import LinkParams
from constellation import generate_walker_tles, write_walker_tle_file
from links import compute_snapshot
from orbit import load_tles, propagate_positions


@dataclass
class ProfileRow:
    n_sats: int
    n_planes: int
    source: str
    max_range_km: float
    duration_s: float
    time_step_s: float
    n_timesteps: int
    # subsystem times (seconds)
    tle_write_s: float = 0.0
    tle_load_s: float = 0.0
    propagate_s: float = 0.0
    topology_brute_s: Optional[float] = None
    topology_spatial_s: float = 0.0
    # topology stats (first snapshot)
    candidate_pairs: int = 0
    link_count: int = 0
    # resources
    peak_rss_mb: Optional[float] = None
    wall_s: float = 0.0
    error: Optional[str] = None
    notes: str = ""


def _rss_mb() -> Optional[float]:
    if psutil is None:
        return None
    return psutil.Process().memory_info().rss / (1024 * 1024)


def _count_spatial_candidates(sat_pos: dict, max_range_km: float) -> int:
    from links import _candidate_pairs_spatial
    names = list(sat_pos.keys())
    return len(_candidate_pairs_spatial(names, sat_pos, max_range_km))


def profile_size(
    n: int,
    n_planes: int,
    max_range_km: float,
    duration_s: float,
    time_step_s: float,
    topology_only: bool,
    run_brute: bool,
    out_dir: Path,
) -> ProfileRow:
    wall0 = time.perf_counter()
    rss0 = _rss_mb()
    row = ProfileRow(
        n_sats=n,
        n_planes=n_planes,
        source="synthetic_walker",
        max_range_km=max_range_km,
        duration_s=duration_s,
        time_step_s=time_step_s,
        n_timesteps=0,
        notes="Synthetic Walker TLEs; not operational Starlink data",
    )
    try:
        tle_path = out_dir / f"walker_{n}.tle"
        t0 = time.perf_counter()
        write_walker_tle_file(tle_path, n_sats=n, n_planes=n_planes)
        row.tle_write_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        sats = load_tles(tle_path, max_sats=n)
        row.tle_load_s = time.perf_counter() - t0
        row.n_sats = len(sats)

        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        times = []
        t = start
        end = start + timedelta(seconds=duration_s)
        while t <= end:
            times.append(t)
            t += timedelta(seconds=time_step_s)
        row.n_timesteps = len(times)

        t0 = time.perf_counter()
        series = propagate_positions(sats, times)
        row.propagate_s = time.perf_counter() - t0

        params = LinkParams(max_range_km=max_range_km)
        # first snapshot positions
        pos0 = {name: series[name][0] for name in series}

        if run_brute and n <= 400:
            t0 = time.perf_counter()
            brute = compute_snapshot(0.0, pos0, {}, params, use_spatial_index=False)
            row.topology_brute_s = time.perf_counter() - t0
        else:
            brute = None

        t0 = time.perf_counter()
        spatial = compute_snapshot(0.0, pos0, {}, params, use_spatial_index=True)
        row.topology_spatial_s = time.perf_counter() - t0
        row.link_count = len(spatial.edges)
        row.candidate_pairs = _count_spatial_candidates(pos0, max_range_km)

        if brute is not None:
            be = {frozenset((e.u, e.v)) for e in brute.edges}
            se = {frozenset((e.u, e.v)) for e in spatial.edges}
            if be != se:
                row.error = "spatial/brute edge mismatch"
                row.notes += "; EQUIVALENCE FAIL"

        # Full topology series cost (spatial only)
        if not topology_only:
            t0 = time.perf_counter()
            for i in range(len(times)):
                pos = {name: series[name][i] for name in series}
                compute_snapshot(i * time_step_s, pos, {}, params, use_spatial_index=True)
            row.topology_spatial_s = time.perf_counter() - t0  # overwrite as full series

        rss1 = _rss_mb()
        if rss0 is not None and rss1 is not None:
            row.peak_rss_mb = max(rss0, rss1)
        row.wall_s = time.perf_counter() - wall0
    except Exception as exc:
        row.error = f"{type(exc).__name__}: {exc}"
        row.wall_s = time.perf_counter() - wall0
        row.peak_rss_mb = _rss_mb()
    return row


def env_manifest() -> dict:
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpus": psutil.cpu_count() if psutil else None,
        "ram_gb": round(psutil.virtual_memory().total / 1e9, 2) if psutil else None,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 4 scale profiling")
    ap.add_argument(
        "--sizes",
        default="10,20,50,100,200,400",
        help="Comma-separated satellite counts",
    )
    ap.add_argument("--planes", type=int, default=10)
    ap.add_argument("--max-range", type=float, default=3000.0)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--time-step", type=float, default=15.0)
    ap.add_argument("--topology-only", action="store_true",
                    help="Only first-snapshot topology timing (faster)")
    ap.add_argument("--no-brute", action="store_true")
    ap.add_argument("--out", default="outputs/phase4_scale")
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows: List[ProfileRow] = []
    print(
        f"{'N':>6} {'prop_s':>8} {'topo_s':>8} {'brute_s':>8} "
        f"{'links':>7} {'cand':>8} {'rss_mb':>8} {'wall_s':>8} {'err'}"
    )
    for n in sizes:
        planes = max(1, min(args.planes, n))
        row = profile_size(
            n=n,
            n_planes=planes,
            max_range_km=args.max_range,
            duration_s=args.duration,
            time_step_s=args.time_step,
            topology_only=args.topology_only,
            run_brute=not args.no_brute,
            out_dir=out,
        )
        rows.append(row)
        brute_s = f"{row.topology_brute_s:.4f}" if row.topology_brute_s is not None else "—"
        rss = f"{row.peak_rss_mb:.1f}" if row.peak_rss_mb is not None else "—"
        err = row.error or ""
        print(
            f"{row.n_sats:6d} {row.propagate_s:8.3f} {row.topology_spatial_s:8.4f} "
            f"{brute_s:>8} {row.link_count:7d} {row.candidate_pairs:8d} "
            f"{rss:>8} {row.wall_s:8.2f} {err}"
        )

    # Write outputs
    csv_path = out / "scale_profile.csv"
    fieldnames = list(asdict(rows[0]).keys()) if rows else []
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))

    manifest = {
        "env": env_manifest(),
        "args": vars(args),
        "n_rows": len(rows),
        "csv": str(csv_path),
        "data_class": "synthetic_walker_unless_noted",
        "disclaimer": (
            "Synthetic Walker TLEs are not operational Starlink data. "
            "Brute topology skipped or limited for large N."
        ),
    }
    (out / "scale_profile_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"Wrote {csv_path}")
    print(f"Env: {manifest['env']}")


if __name__ == "__main__":
    main()
