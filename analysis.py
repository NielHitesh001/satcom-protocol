"""
analysis.py – Produce summary plots from metrics files.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import matplotlib.pyplot as plt
import pandas as pd


def plot_delivery_ratio(csv_paths: List[str | Path], out_path: str | Path) -> None:
    """Bar chart of delivery ratio per protocol / run."""
    rows = []
    for p in csv_paths:
        df = pd.read_csv(p)
        name = Path(p).stem.replace("_flows", "")
        total_sent = df["sent"].sum()
        total_del = df["delivered"].sum()
        ratio = total_del / total_sent if total_sent else 0.0
        rows.append({"run": name, "delivery_ratio": ratio})
    data = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(data["run"], data["delivery_ratio"], color="#243044")
    ax.set_ylabel("Delivery Ratio")
    ax.set_ylim(0, 1.05)
    ax.set_title("End-to-End Delivery Ratio")
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_latency(csv_paths: List[str | Path], out_path: str | Path) -> None:
    rows = []
    for p in csv_paths:
        df = pd.read_csv(p)
        name = Path(p).stem.replace("_flows", "")
        mean_lat = df["mean_latency_s"].mean()
        rows.append({"run": name, "mean_latency_s": mean_lat})
    data = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(data["run"], data["mean_latency_s"], color="#c5d5cc")
    ax.set_ylabel("Mean Latency (s)")
    ax.set_title("Average Path Latency")
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_control_overhead(manifest_paths: List[str | Path], out_path: str | Path) -> None:
    import json
    rows = []
    for p in manifest_paths:
        data = json.loads(Path(p).read_text())
        rows.append({
            "run": Path(p).stem.replace("_manifest", ""),
            "control_sent": data.get("control_sent", 0),
        })
    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(df["run"], df["control_sent"], color="#e6dcc8")
    ax.set_ylabel("Control Messages Sent")
    ax.set_title("Control Overhead")
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
