"""
metrics.py – Collect and export run metrics.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class FlowMetrics:
    flow_id: str
    src: str
    dst: str
    sent: int = 0
    delivered: int = 0
    dropped: int = 0
    latencies_s: List[float] = field(default_factory=list)
    hop_counts: List[int] = field(default_factory=list)

    @property
    def delivery_ratio(self) -> float:
        return self.delivered / self.sent if self.sent else 0.0

    @property
    def mean_latency(self) -> float:
        return sum(self.latencies_s) / len(self.latencies_s) if self.latencies_s else 0.0

    @property
    def jitter(self) -> float:
        if len(self.latencies_s) < 2:
            return 0.0
        mean = self.mean_latency
        var = sum((x - mean) ** 2 for x in self.latencies_s) / (len(self.latencies_s) - 1)
        return var ** 0.5


@dataclass
class RunMetrics:
    scenario_name: str
    protocol: str
    seed: int
    tle_snapshot_id: str
    duration_s: float
    flows: Dict[str, FlowMetrics] = field(default_factory=dict)
    control_messages_sent: int = 0
    control_messages_recv: int = 0
    peak_state_bytes: Dict[str, int] = field(default_factory=dict)
    route_changes: int = 0
    discovery_times_s: List[float] = field(default_factory=list)

    def summary_dict(self) -> dict:
        total_sent = sum(f.sent for f in self.flows.values())
        total_del = sum(f.delivered for f in self.flows.values())
        all_lat = [l for f in self.flows.values() for l in f.latencies_s]
        return {
            "scenario": self.scenario_name,
            "protocol": self.protocol,
            "seed": self.seed,
            "tle_id": self.tle_snapshot_id,
            "duration_s": self.duration_s,
            "packets_sent": total_sent,
            "packets_delivered": total_del,
            "delivery_ratio": total_del / total_sent if total_sent else 0.0,
            "mean_latency_s": sum(all_lat) / len(all_lat) if all_lat else 0.0,
            "control_sent": self.control_messages_sent,
            "control_recv": self.control_messages_recv,
            "route_changes": self.route_changes,
            "mean_discovery_s": (
                sum(self.discovery_times_s) / len(self.discovery_times_s)
                if self.discovery_times_s else 0.0
            ),
            "peak_state_bytes_total": sum(self.peak_state_bytes.values()),
        }

    def to_csv(self, path: str | Path) -> None:
        path = Path(path)
        rows = []
        for fid, fm in self.flows.items():
            rows.append({
                "flow_id": fid,
                "src": fm.src,
                "dst": fm.dst,
                "sent": fm.sent,
                "delivered": fm.delivered,
                "dropped": fm.dropped,
                "delivery_ratio": fm.delivery_ratio,
                "mean_latency_s": fm.mean_latency,
                "jitter_s": fm.jitter,
                "mean_hops": sum(fm.hop_counts) / len(fm.hop_counts) if fm.hop_counts else 0.0,
            })
        pd.DataFrame(rows).to_csv(path, index=False)

    def write_manifest(self, path: str | Path, extra: Optional[dict] = None) -> None:
        data = self.summary_dict()
        if extra:
            data.update(extra)
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
