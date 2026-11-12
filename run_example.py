"""Run the example scenario for all three protocols and print a short summary."""

from __future__ import annotations

import sys
from pathlib import Path

from engine import run_scenario
from analysis import plot_delivery_ratio, plot_latency, plot_control_overhead


def main() -> None:
    scenario = Path(__file__).parent / "scenarios" / "example.yaml"
    out_dir = Path(__file__).parent / "outputs"
    out_dir.mkdir(exist_ok=True)

    protocols = ["predictive", "olsr", "aodv"]
    flow_csvs = []
    manifests = []

    for proto in protocols:
        # Patch the protocol field on the fly by writing a temporary scenario
        import yaml
        data = yaml.safe_load(scenario.read_text())
        data["protocol"] = proto
        data["name"] = f"example_{proto}"
        tmp = out_dir / f"scenario_{proto}.yaml"
        tmp.write_text(yaml.dump(data))

        print(f"\n=== Running protocol: {proto} ===")
        metrics = run_scenario(tmp, output_dir=out_dir)
        summary = metrics.summary_dict()
        for k, v in summary.items():
            print(f"  {k}: {v}")
        flow_csvs.append(out_dir / f"{data['name']}_{proto}_{data['seed']}_flows.csv")
        manifests.append(out_dir / f"{data['name']}_{proto}_{data['seed']}_manifest.json")

    # Plots
    try:
        plot_delivery_ratio(flow_csvs, out_dir / "delivery_ratio.png")
        plot_latency(flow_csvs, out_dir / "latency.png")
        plot_control_overhead(manifests, out_dir / "control_overhead.png")
        print(f"\nPlots written to {out_dir}")
    except Exception as e:
        print(f"Plotting skipped: {e}")


if __name__ == "__main__":
    main()
