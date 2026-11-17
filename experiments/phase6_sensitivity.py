"""
One-factor sensitivity runs. Synthetic Walker, sequential, small by default.

Each row changes exactly one parameter from the baseline. Workload fingerprint
excludes the swept field and the protocol name so matched protocols can be
compared inside a factor level.

Usage:
  PYTHONPATH=. python -m experiments.phase6_sensitivity
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import yaml

try:
    import psutil
except ImportError:
    psutil = None

from constellation import write_walker_tle_file
from engine import run_scenario


BASE = {
    "max_range_km": 5000.0,
    "capacity_mbps": 100.0,
    "loss_prob": 0.0,
    "queue_limit": 64,
    "time_step_s": 15.0,
    "rate_pps": 0.5,
    "packet_size_bytes": 512,
}

SWEEPS = {
    "max_range_km": [3000.0, 5000.0],
    "capacity_mbps": [10.0, 100.0],
    "loss_prob": [0.0, 0.05],
    "queue_limit": [4, 64],
    "time_step_s": [15.0, 30.0],
    "rate_pps": [0.5, 1.0],
    "packet_size_bytes": [256, 1500],
}


def _fp(data: dict, ignore: str) -> str:
    payload = {k: v for k, v in data.items() if k not in ("protocol", "name", ignore)}
    # link and traffic embed the swept value; strip by copying structure in caller
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--duration", type=float, default=45.0)
    ap.add_argument("--protocols", default="predictive,aodv")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--factors", default="time_step_s,capacity_mbps,loss_prob")
    ap.add_argument("--out", default="outputs/phase6_sensitivity")
    args = ap.parse_args()

    protocols = [p.strip() for p in args.protocols.split(",") if p.strip()]
    factors = [f.strip() for f in args.factors.split(",") if f.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tle = out / f"walker_{args.n}.tle"
    if not tle.exists():
        write_walker_tle_file(tle, n_sats=args.n, n_planes=6)

    src = "SYN-LEO-90001"
    dst = f"SYN-LEO-{90000 + max(2, args.n // 2)}"
    rows = []
    rss0 = psutil.Process().memory_info().rss / 1e6 if psutil else None

    print(f"{'factor':16} {'value':>8} {'proto':12} {'del':>6} {'ctrl':>7} {'air_B':>8} {'wall':>6}")
    for factor in factors:
        for value in SWEEPS[factor]:
            cfg = dict(BASE)
            cfg[factor] = value
            for proto in protocols:
                data = {
                    "name": f"p6_{factor}_{value}_{proto}",
                    "start_time_utc": "2026-01-01T00:00:00Z",
                    "duration_s": args.duration,
                    "time_step_s": cfg["time_step_s"],
                    "tle_path": str(tle.resolve()),
                    "tle_snapshot_id": f"synthetic_walker_n{args.n}",
                    "max_sats": args.n,
                    "protocol": proto,
                    "seed": args.seed,
                    "ground_sites": [],
                    "link": {
                        "max_range_km": cfg["max_range_km"],
                        "min_elevation_deg": 10.0,
                        "capacity_mbps": cfg["capacity_mbps"],
                        "loss_prob": cfg["loss_prob"],
                        "queue_limit": cfg["queue_limit"],
                    },
                    "traffic": [
                        {
                            "flow_id": "f0",
                            "source": src,
                            "destination": dst,
                            "start_s": 5.0,
                            "duration_s": max(10.0, args.duration - 15),
                            "rate_pps": cfg["rate_pps"],
                            "packet_size_bytes": cfg["packet_size_bytes"],
                        }
                    ],
                    "control_sizes": ControlSizeNote(),
                }
                # fingerprint of inputs except protocol
                fp_src = {k: data[k] for k in data if k not in ("protocol", "name")}
                fp = hashlib.sha256(
                    json.dumps(fp_src, sort_keys=True, default=str).encode()
                ).hexdigest()[:12]
                scn = out / f"scn_{factor}_{value}_{proto}.yaml"
                scn.write_text(yaml.dump(data))
                t0 = time.perf_counter()
                try:
                    m = run_scenario(scn, output_dir=out)
                    wall = time.perf_counter() - t0
                    s = m.summary_dict()
                    cons = (
                        s["packets_sent"]
                        - s["packets_delivered"]
                        - s["packets_dropped"]
                        - s["packets_residual"]
                    )
                    row = {
                        "factor": factor,
                        "value": value,
                        "protocol": proto,
                        "seed": args.seed,
                        "n_sats": args.n,
                        "delivery_ratio": s["delivery_ratio"],
                        "mean_latency_s": s["mean_latency_s"],
                        "control_sent": s["control_sent"],
                        "control_bytes_sent": s["control_bytes_sent"],
                        "control_airtime_bytes": s["control_airtime_bytes"],
                        "control_serialization_s": s["control_serialization_s"],
                        "control_bytes_estimated_not_airtime": s[
                            "control_bytes_estimated_not_airtime"
                        ],
                        "packets_sent": s["packets_sent"],
                        "packets_residual": s["packets_residual"],
                        "conservation_ok": cons == 0,
                        "wall_s": wall,
                        "workload_fp": fp,
                        "data_class": "synthetic_walker",
                        "oracle": proto == "predictive",
                    }
                    rows.append(row)
                    print(
                        f"{factor:16} {value!s:>8} {proto:12} {s['delivery_ratio']:6.2f} "
                        f"{s['control_sent']:7d} {s['control_airtime_bytes']:8d} {wall:6.2f}"
                    )
                except Exception as exc:
                    print(f"{factor} {value} {proto} ERROR {exc}")
                    rows.append({"factor": factor, "value": value, "protocol": proto, "error": str(exc)})

    rss1 = psutil.Process().memory_info().rss / 1e6 if psutil else None
    if rows:
        keys = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        csv_path = out / "sensitivity.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        manifest = {
            "env": {
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "cpus": psutil.cpu_count() if psutil else None,
                "ram_gb": round(psutil.virtual_memory().total / 1e9, 2) if psutil else None,
                "rss_mb_end": rss1,
                "rss_mb_start": rss0,
            },
            "baseline": BASE,
            "factors": factors,
            "disclaimer": "Synthetic Walker. One factor varies per row family. Oracle is not deployable.",
            "csv": str(csv_path),
        }
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print("Wrote", csv_path)


def ControlSizeNote():
    """Default size model recorded in every scenario for metadata."""
    return {
        "aodv_rreq": 64,
        "aodv_rrep": 64,
        "aodv_rerr_base": 48,
        "aodv_rerr_per_dest": 8,
        "olsr_hello": 48,
        "olsr_tc": 64,
        "practical_refresh_per_node": 32,
    }


if __name__ == "__main__":
    main()
