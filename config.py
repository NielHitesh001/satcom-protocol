"""
config.py – Load and validate scenario files (YAML / JSON).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class GroundSite:
    name: str
    lat_deg: float
    lon_deg: float
    alt_m: float = 0.0


@dataclass
class TrafficFlow:
    flow_id: str
    source: str
    destination: str
    start_s: float
    duration_s: float
    rate_pps: float
    packet_size_bytes: int = 512
    arrival_model: str = "deterministic"  # deterministic or poisson


@dataclass
class LinkParams:
    max_range_km: float = 5000.0
    min_elevation_deg: float = 25.0
    capacity_mbps: float = 100.0
    loss_prob: float = 0.0
    queue_limit: int = 64
    max_isl_degree: Optional[int] = None


@dataclass
class Scenario:
    name: str
    start_time_utc: str
    duration_s: float
    time_step_s: float
    tle_path: str
    tle_snapshot_id: str
    ground_sites: List[GroundSite]
    traffic: List[TrafficFlow]
    link: LinkParams
    protocol: str
    seed: int = 42
    sat_subset: Optional[List[str]] = None
    max_sats: Optional[int] = 50

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Scenario":
        sites = [GroundSite(**s) for s in d.get("ground_sites", [])]
        flows = [TrafficFlow(**f) for f in d.get("traffic", [])]
        link = LinkParams(**d.get("link", {}))
        return cls(
            name=d.get("name", "unnamed"),
            start_time_utc=d["start_time_utc"],
            duration_s=float(d["duration_s"]),
            time_step_s=float(d.get("time_step_s", 10.0)),
            tle_path=d["tle_path"],
            tle_snapshot_id=d.get("tle_snapshot_id", "unknown"),
            ground_sites=sites,
            traffic=flows,
            link=link,
            protocol=d.get("protocol", "aodv").lower(),
            seed=int(d.get("seed", 42)),
            sat_subset=d.get("sat_subset"),
            max_sats=d.get("max_sats", 50),
        )


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        raise ValueError(f"Unsupported scenario format: {path.suffix}")
    return Scenario.from_dict(data)
