"""
Phase 5 experiment matrix — fair protocol comparison with optional
connectivity-aware endpoints.

All protocols in one matrix cell share:
  - same TLE / constellation size
  - same seed
  - same duration, time_step, link params
  - same traffic injection times and packet sizes
  - same endpoints (fixed or connectivity-aware choice frozen once per cell)

Usage:
  PYTHONPATH=. python -m experiments.phase5_matrix \\
    --sizes 20,50 --protocols predictive,aodv,olsr,practical_predictive \\
    --traffic-mode connectivity_aware --seeds 42,43 --duration 60
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import yaml

try:
    import psutil
except ImportError:
    psutil = None

from config import LinkParams
from connectivity import EndpointChoice, fixed_endpoints, select_endpoints_connectivity_aware
from constellation import write_walker_tle_file
from engine import run_scenario
from links import compute_snapshot
from orbit import load_tles, propagate_positions


def _env() -> dict:
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpus": psutil.cpu_count() if psutil else None,
        "ram_gb": round(psutil.virtual_memory().total / 1e9, 2) if psutil else None,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def _workload_fingerprint(data: dict) -> str:
    """Hash experiment inputs excluding protocol name."""
    payload = {
        k: data[k]
        for k in sorted(data)
        if k not in ("protocol", "name")
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _topology_at(
    tle_path: Path,
    n: int,
    start_utc: str,
    time_s: float,
    max_range_km: float,
) -> Any:
    sats = load_tles(tle_path, max_sats=n)
    start = datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
    t = start + timedelta(seconds=time_s)
    series = propagate_positions(sats, [t])
    pos = {name: series[name][0] for name in series}
    return compute_snapshot(
        time_s, pos, {}, LinkParams(max_range_km=max_range_km), use_spatial_index=True
    )


def build_cell_scenario(
    out_dir: Path,
    n: int,
    planes: int,
    protocol: str,
    seed: int,
    duration_s: float,
    time_step_s: float,
    max_range_km: float,
    rate_pps: float,
    traffic_mode: str,
    min_component_size: int,
    fixed_src: Optional[str],
    fixed_dst: Optional[str],
    endpoint: Optional[EndpointChoice],
) -> tuple[Path, EndpointChoice, str]:
    tle = out_dir / f"walker_{n}.tle"
    if not tle.exists():
        write_walker_tle_file(tle, n_sats=n, n_planes=planes)

    inject_t = 5.0
    if endpoint is None:
        rng = np.random.default_rng(seed)
        if traffic_mode == "connectivity_aware":
            snap = _topology_at(tle, n, "2026-01-01T00:00:00Z", inject_t, max_range_km)
            endpoint = select_endpoints_connectivity_aware(
                snap, rng, min_component_size=min_component_size, injection_time_s=inject_t
            )
            if endpoint is None:
                # Fall back: fixed synthetic names (may disconnect)
                endpoint = fixed_endpoints(
                    f"SYN-LEO-{90001}", f"SYN-LEO-{90000 + max(2, n // 2)}",
                    snap, inject_t,
                )
                endpoint.notes += "; fallback_fixed_no_eligible_component"
        else:
            src = fixed_src or f"SYN-LEO-{90001}"
            dst = fixed_dst or f"SYN-LEO-{90000 + max(2, n // 2)}"
            snap = _topology_at(tle, n, "2026-01-01T00:00:00Z", inject_t, max_range_km)
            endpoint = fixed_endpoints(src, dst, snap, inject_t)

    data = {
        "name": f"p5_{protocol}_{n}_{seed}_{traffic_mode}",
        "start_time_utc": "2026-01-01T00:00:00Z",
        "duration_s": duration_s,
        "time_step_s": time_step_s,
        "tle_path": str(tle.resolve()),
        "tle_snapshot_id": f"synthetic_walker_n{n}",
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
        "traffic": [
            {
                "flow_id": "f0",
                "source": endpoint.source,
                "destination": endpoint.destination,
                "start_s": inject_t,
                "duration_s": max(10.0, duration_s - 20),
                "rate_pps": rate_pps,
                "packet_size_bytes": 512,
            }
        ],
    }
    fp = _workload_fingerprint(data)
    scn = out_dir / f"scn_{protocol}_{n}_{seed}_{traffic_mode}.yaml"
    scn.write_text(yaml.dump(data))
    return scn, endpoint, fp


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 5 fair experiment matrix")
    ap.add_argument("--sizes", default="20,50")
    ap.add_argument(
        "--protocols",
        default="predictive,practical_predictive,aodv,olsr",
    )
    ap.add_argument("--seeds", default="42,43")
    ap.add_argument("--traffic-mode", choices=("fixed", "connectivity_aware"), default="connectivity_aware")
    ap.add_argument("--min-component-size", type=int, default=3)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--time-step", type=float, default=15.0)
    ap.add_argument("--max-range", type=float, default=5000.0)
    ap.add_argument("--rate", type=float, default=0.5)
    ap.add_argument("--planes", type=int, default=10)
    ap.add_argument("--out", default="outputs/phase5_matrix")
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    protocols = [p.strip() for p in args.protocols.split(",") if p.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows: List[dict] = []
    print(
        f"{'proto':22s} {'N':>4} {'mode':>18} {'seed':>4} {'del':>5} "
        f"{'res':>4} {'ctrl':>6} {'comp':>4} {'fp':>8}"
    )

    for n in sizes:
        for seed in seeds:
            # Freeze endpoints once per (n, seed, mode) so all protocols match
            shared_endpoint: Optional[EndpointChoice] = None
            shared_fp: Optional[str] = None

            for proto in protocols:
                scn, endpoint, fp = build_cell_scenario(
                    out, n, args.planes, proto, seed,
                    args.duration, args.time_step, args.max_range, args.rate,
                    args.traffic_mode, args.min_component_size,
                    None, None, shared_endpoint,
                )
                if shared_endpoint is None:
                    shared_endpoint = endpoint
                if shared_fp is None:
                    shared_fp = fp
                elif fp != shared_fp:
                    print(f"FAIRNESS WARNING: fingerprint mismatch {proto} {fp} vs {shared_fp}")

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
                        **s,
                        "n_sats": n,
                        "wall_s": wall,
                        "traffic_mode": endpoint.mode,
                        "endpoint_src": endpoint.source,
                        "endpoint_dst": endpoint.destination,
                        "component_size": endpoint.component_size,
                        "n_components": endpoint.n_components,
                        "endpoint_notes": endpoint.notes,
                        "workload_fp": fp,
                        "conservation_ok": cons == 0,
                        "data_class": "synthetic_walker",
                        "oracle_label": (
                            "optimistic_benchmark"
                            if proto in ("predictive", "oracle", "predictive_oracle")
                            else "candidate"
                        ),
                    }
                    rows.append(row)
                    print(
                        f"{proto:22s} {n:4d} {endpoint.mode:18s} {seed:4d} "
                        f"{s['delivery_ratio']:5.2f} {s['packets_residual']:4d} "
                        f"{s['control_sent']:6d} {endpoint.component_size:4d} {fp}"
                    )
                except Exception as exc:
                    print(f"{proto:22s} {n:4d} ERROR {exc}")
                    rows.append({
                        "protocol": proto, "n_sats": n, "seed": seed,
                        "error": str(exc), "traffic_mode": args.traffic_mode,
                    })

    if rows:
        keys: List[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        csv_path = out / "matrix_results.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        manifest = {
            "env": _env(),
            "args": vars(args),
            "disclaimer": "Synthetic Walker; connectivity-aware ≠ guaranteed delivery",
            "fairness": "Same workload_fp across protocols within (n, seed, mode) cell",
            "csv": str(csv_path),
        }
        (out / "matrix_manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
