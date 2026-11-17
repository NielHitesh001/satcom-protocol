"""
Measure OLSR control structure and AODV residual/route-fail events on a
fixed fair workload (connectivity-aware).

Does not modify protocol implementations — observation only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.phase5_matrix import build_cell_scenario
from engine import Simulator
from config import load_scenario
from routing.olsr import OLSRProtocol
from routing.aodv import AODVProtocol


def probe(protocol: str, n: int, seed: int, out: Path) -> dict:
    scn, endpoint, fp = build_cell_scenario(
        out, n, 10, protocol, seed, 60.0, 15.0, 5000.0, 0.5,
        "connectivity_aware", 3, None, None, None,
    )
    scenario = load_scenario(scn)
    sim = Simulator(scenario)
    metrics = sim.run()
    s = metrics.summary_dict()
    report = {
        "protocol": protocol,
        "n_sats": n,
        "seed": seed,
        "endpoint": {
            "src": endpoint.source,
            "dst": endpoint.destination,
            "component_size": endpoint.component_size,
            "mode": endpoint.mode,
        },
        "workload_fp": fp,
        "summary": s,
        "control_per_sim_second": (
            s["control_sent"] / scenario.duration_s if scenario.duration_s else None
        ),
        "control_per_sat": (
            s["control_sent"] / n if n else None
        ),
    }
    if isinstance(sim.protocol, OLSRProtocol):
        proto = sim.protocol
        report["olsr"] = {
            "hello_interval_s": proto.hello_interval_s,
            "tc_interval_s": proto.tc_interval_s,
            "hold_time_s": proto.hold_time_s,
            "note": (
                "Control count includes HELLO and TC emissions and TC retransmits "
                "by MPR selectors; dense graphs increase HELLO fanout and TC flood."
            ),
        }
    if isinstance(sim.protocol, AODVProtocol):
        proto = sim.protocol
        fails = [e for e in proto.route_events if e.event_type == "route_fail"]
        disc = [e for e in proto.route_events if e.event_type == "discovery_start"]
        ends = [e for e in proto.route_events if e.event_type == "discovery_end"]
        buffered = sum(len(pkts) for d in proto.buffer.values() for pkts in d.values())
        report["aodv"] = {
            "route_fail_events": len(fails),
            "discovery_starts": len(disc),
            "discovery_ends": len(ends),
            "buffer_packets_at_end": buffered,
            "residuals_in_metrics": s.get("packets_residual"),
            "note": (
                "Residuals include buffered DATA after RERR/invalidation; "
                "not classified as network drops."
            ),
        }
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="outputs/phase5_probe")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for proto in ("olsr", "aodv", "predictive"):
        r = probe(proto, args.n, args.seed, out)
        reports.append(r)
        print(proto, "ctrl", r["summary"].get("control_sent"),
              "del", r["summary"].get("delivery_ratio"),
              "res", r["summary"].get("packets_residual"))
    path = out / "protocol_probe.json"
    path.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print("Wrote", path)


if __name__ == "__main__":
    main()
