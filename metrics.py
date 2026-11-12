"""
metrics.py – Collect and export run metrics.

Missing latency observations are reported as null/NaN, never as 0.0.
Packet fates: sent, delivered, dropped (network/queue/loss), residual (in-flight at end).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


def _percentile(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    k = (len(ys) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return ys[int(k)]
    return ys[f] * (c - k) + ys[c] * (k - f)


@dataclass
class FlowMetrics:
    flow_id: str
    src: str
    dst: str
    sent: int = 0
    delivered: int = 0
    dropped: int = 0
    residual: int = 0  # still in-flight / buffered at sim end
    latencies_s: List[float] = field(default_factory=list)
    hop_counts: List[int] = field(default_factory=list)

    @property
    def delivery_ratio(self) -> float:
        return self.delivered / self.sent if self.sent else 0.0

    @property
    def loss_ratio(self) -> float:
        """Dropped / sent (excludes residual)."""
        return self.dropped / self.sent if self.sent else 0.0

    @property
    def mean_latency(self) -> Optional[float]:
        return sum(self.latencies_s) / len(self.latencies_s) if self.latencies_s else None

    @property
    def median_latency(self) -> Optional[float]:
        return _percentile(self.latencies_s, 50)

    @property
    def p95_latency(self) -> Optional[float]:
        return _percentile(self.latencies_s, 95)

    @property
    def p99_latency(self) -> Optional[float]:
        return _percentile(self.latencies_s, 99)

    @property
    def jitter(self) -> Optional[float]:
        if len(self.latencies_s) < 2:
            return None
        mean = self.mean_latency or 0.0
        var = sum((x - mean) ** 2 for x in self.latencies_s) / (len(self.latencies_s) - 1)
        return var ** 0.5

    @property
    def mean_hops(self) -> Optional[float]:
        return sum(self.hop_counts) / len(self.hop_counts) if self.hop_counts else None


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
    control_bytes_sent: int = 0
    peak_state_bytes: Dict[str, int] = field(default_factory=dict)
    route_changes: int = 0
    discovery_times_s: List[float] = field(default_factory=list)
    events_processed: int = 0

    def summary_dict(self) -> dict:
        total_sent = sum(f.sent for f in self.flows.values())
        total_del = sum(f.delivered for f in self.flows.values())
        total_drop = sum(f.dropped for f in self.flows.values())
        total_res = sum(f.residual for f in self.flows.values())
        all_lat = [l for f in self.flows.values() for l in f.latencies_s]
        all_hops = [h for f in self.flows.values() for h in f.hop_counts]

        mean_lat = sum(all_lat) / len(all_lat) if all_lat else None
        return {
            "scenario": self.scenario_name,
            "protocol": self.protocol,
            "seed": self.seed,
            "tle_id": self.tle_snapshot_id,
            "duration_s": self.duration_s,
            "packets_sent": total_sent,
            "packets_delivered": total_del,
            "packets_dropped": total_drop,
            "packets_residual": total_res,
            "delivery_ratio": total_del / total_sent if total_sent else 0.0,
            "loss_ratio": total_drop / total_sent if total_sent else 0.0,
            "mean_latency_s": mean_lat,
            "median_latency_s": _percentile(all_lat, 50),
            "p95_latency_s": _percentile(all_lat, 95),
            "p99_latency_s": _percentile(all_lat, 99),
            "mean_hops": sum(all_hops) / len(all_hops) if all_hops else None,
            "control_sent": self.control_messages_sent,
            "control_recv": self.control_messages_recv,
            "control_bytes_sent": self.control_bytes_sent,
            "route_changes": self.route_changes,
            "mean_discovery_s": (
                sum(self.discovery_times_s) / len(self.discovery_times_s)
                if self.discovery_times_s
                else None
            ),
            "peak_state_bytes_total": sum(self.peak_state_bytes.values()),
            "events_processed": self.events_processed,
        }

    def to_csv(self, path: str | Path) -> None:
        path = Path(path)
        rows = []
        for fid, fm in self.flows.items():
            rows.append(
                {
                    "flow_id": fid,
                    "src": fm.src,
                    "dst": fm.dst,
                    "sent": fm.sent,
                    "delivered": fm.delivered,
                    "dropped": fm.dropped,
                    "residual": fm.residual,
                    "delivery_ratio": fm.delivery_ratio,
                    "loss_ratio": fm.loss_ratio,
                    "mean_latency_s": fm.mean_latency,
                    "median_latency_s": fm.median_latency,
                    "p95_latency_s": fm.p95_latency,
                    "p99_latency_s": fm.p99_latency,
                    "jitter_s": fm.jitter,
                    "mean_hops": fm.mean_hops,
                }
            )
        pd.DataFrame(rows).to_csv(path, index=False)

    def write_manifest(self, path: str | Path, extra: Optional[dict] = None) -> None:
        data = self.summary_dict()
        if extra:
            data.update(extra)

        def _default(o):
            if o is None:
                return None
            if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
                return None
            raise TypeError(type(o))

        Path(path).write_text(json.dumps(data, indent=2, default=_default), encoding="utf-8")
