"""Scale experiment: vary satellite population."""
from __future__ import annotations
import argparse, csv, time
from pathlib import Path
import yaml
from engine import run_scenario

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="scenarios/example.yaml")
    ap.add_argument("--sizes", default="10,15,20,30")
    ap.add_argument("--protocols", default="predictive,practical_predictive,aodv")
    ap.add_argument("--out", default="outputs/scale")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--duration", type=float, default=None)
    args = ap.parse_args()
    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    protocols = [p.strip() for p in args.protocols.split(",") if p.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base_path = Path(args.scenario)
    base = yaml.safe_load(base_path.read_text())
    rows = []
    print(f"{'proto':22s} {'N':>4} {'del_r':>6} {'lat':>8} {'ctrl':>7} {'wall_s':>7}")
    for n in sizes:
        for proto in protocols:
            data = dict(base)
            data["protocol"] = proto
            data["max_sats"] = n
            data["seed"] = args.seed
            data["name"] = f"scale_{proto}_{n}"
            if args.duration is not None:
                data["duration_s"] = args.duration
            tle = Path(data["tle_path"])
            if not tle.is_absolute():
                for root in (base_path.parent, base_path.parent.parent, Path.cwd()):
                    cand = (root / tle).resolve()
                    if cand.exists():
                        data["tle_path"] = str(cand)
                        break
            scn = out / f"scn_{proto}_{n}.yaml"
            scn.write_text(yaml.dump(data))
            t0 = time.perf_counter()
            m = run_scenario(scn, output_dir=out)
            wall = time.perf_counter() - t0
            s = m.summary_dict()
            lat = s["mean_latency_s"]
            lat_s = f"{lat:.4f}" if lat is not None else "None"
            print(f"{proto:22s} {n:4d} {s['delivery_ratio']:6.3f} {lat_s:>8} {s['control_sent']:7d} {wall:7.2f}")
            rows.append({**s, "max_sats": n, "wall_s": wall})
    if rows:
        csv_path = out / "scale_results.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {csv_path}")

if __name__ == "__main__":
    main()
