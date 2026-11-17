"""
Full simulation scale runs on synthetic Walker constellations.

Writes temporary TLE + scenario, runs protocols, records metrics and wall time.
Synthetic data is labeled in the manifest.

Usage:
  PYTHONPATH=. python -m experiments.full_scale_sim --sizes 20,50 --protocols predictive,aodv
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

try:
    import psutil
except ImportError:
    psutil = None

from constellation import write_walker_tle_file
from engine import run_scenario


def env_info() -> dict:
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpus": psutil.cpu_count() if psutil else None,
        "ram_gb": round(psutil.virtual_memory().total / 1e9, 2) if psutil else None,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def make_scenario(
    out_dir: Path,
    n: int,
    planes: int,
    protocol: str,
    seed: int,
    duration_s: float,
    time_step_s: float,
    max_range_km: float,
    rate_pps: float,
    n_flows: int,
) -> Path:
    tle = out_dir / f"walker_{n}.tle"
    if not tle.exists():
        write_walker_tle_file(tle, n_sats=n, n_planes=planes)
    # Pick traffic endpoints from generated names (NORAD 90001+)
    # Names are SYN-LEO-{norad}
    sats_per = max(1, n // planes)
    # Use first and a mid-constellation name
    src = f"SYN-LEO-{90001}"
    dst_id = 90000 + max(2, n // 2)
    dst = f"SYN-LEO-{dst_id}"

    traffic = []
    for i in range(n_flows):
        traffic.append({
            "flow_id": f"f{i}",
            "source": src,
            "destination": dst,
            "start_s": 5.0,
            "duration_s": max(10.0, duration_s - 20),
            "rate_pps": rate_pps,
            "packet_size_bytes": 512,
        })

    data = {
        "name": f"full_{protocol}_{n}_{seed}",
        "start_time_utc": "2026-01-01T00:00:00Z",
        "duration_s": duration_s,
        "time_step_s": time_step_s,
        "tle_path": str(tle.resolve()),
        "tle_snapshot_id": f"synthetic_walker_n{n}_p{planes}",
        "max_sats": n,
        "protocol": protocol,
        "seed": seed,
        "ground_sites": [],
        "link": {
            "max_range_km": max_range_km,
            "min_elevation_deg": 25.0,
            "capacity_mbps": 100.0,
            "loss_prob": 0.0,
            "queue_limit": 64,
        },
        "traffic": traffic,
    }
    scn = out_dir / f"scn_{protocol}_{n}_{seed}.yaml"
    scn.write_text(yaml.dump(data))
    return scn


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="20,50")
    ap.add_argument("--protocols", default="predictive,practical_predictive,aodv")
    ap.add_argument("--seeds", default="42")
    ap.add_argument("--duration", type=float, default=90.0)
    ap.add_argument("--time-step", type=float, default=15.0)
    ap.add_argument("--max-range", type=float, default=5000.0)
    ap.add_argument("--planes", type=int, default=10)
    ap.add_argument("--rate", type=float, default=0.5)
    ap.add_argument("--flows", type=int, default=1)
    ap.add_argument("--out", default="outputs/phase4_fullsim")
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    protocols = [p.strip() for p in args.protocols.split(",") if p.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    print(f"{'proto':22s} {'N':>4} {'seed':>5} {'del_r':>6} {'res':>4} {'ctrl':>7} {'wall':>7}")

    for n in sizes:
        for proto in protocols:
            for seed in seeds:
                scn = make_scenario(
                    out, n, args.planes, proto, seed,
                    args.duration, args.time_step, args.max_range,
                    args.rate, args.flows,
                )
                rss0 = psutil.Process().memory_info().rss / 1e6 if psutil else None
                t0 = time.perf_counter()
                try:
                    m = run_scenario(scn, output_dir=out)
                    wall = time.perf_counter() - t0
                    s = m.summary_dict()
                    rss1 = psutil.Process().memory_info().rss / 1e6 if psutil else None
                    cons = (
                        s["packets_sent"]
                        - s["packets_delivered"]
                        - s["packets_dropped"]
                        - s["packets_residual"]
                    )
                    row = {
                        **s,
                        "n_sats": n,
                        "wall_s": wall,
                        "rss_mb": max(rss0 or 0, rss1 or 0) if rss0 else None,
                        "conservation_ok": cons == 0,
                        "data_class": "synthetic_walker",
                    }
                    rows.append(row)
                    print(
                        f"{proto:22s} {n:4d} {seed:5d} {s['delivery_ratio']:6.3f} "
                        f"{s['packets_residual']:4d} {s['control_sent']:7d} {wall:7.2f}"
                    )
                except Exception as exc:
                    wall = time.perf_counter() - t0
                    print(f"{proto:22s} {n:4d} {seed:5d} ERROR {exc}")
                    rows.append({
                        "protocol": proto,
                        "n_sats": n,
                        "seed": seed,
                        "error": str(exc),
                        "wall_s": wall,
                        "data_class": "synthetic_walker",
                    })

    if rows:
        # union of keys
        keys: List[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        csv_path = out / "full_scale_results.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        manifest = {
            "env": env_info(),
            "args": vars(args),
            "disclaimer": "Synthetic Walker constellation; not operational Starlink TLEs",
            "csv": str(csv_path),
        }
        (out / "full_scale_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
