"""
links.py – Compute time-varying satellite / ground topology.

A link exists when:
  1. Line-of-sight is clear (segment does not intersect Earth)
  2. Range <= max_range_km
  3. For ground↔sat: elevation >= min_elevation_deg

Outputs a sequence of topology snapshots or change events that the
engine can consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from config import LinkParams
from orbit import WGS84_A, ground_site_ecef


C_LIGHT_KM_S = 299792.458  # km/s


@dataclass
class LinkEdge:
    u: str
    v: str
    delay_s: float
    capacity_mbps: float
    distance_km: float


@dataclass
class TopologySnapshot:
    time_s: float
    edges: List[LinkEdge]
    # adjacency for quick lookup
    adj: Dict[str, Set[str]]


def los_clear(p1: np.ndarray, p2: np.ndarray, earth_radius_km: float = WGS84_A) -> bool:
    """
    True if the straight-line segment between p1 and p2 does not come closer
    to the Earth centre than the Earth radius (approximate LOS check).
    """
    d = p2 - p1
    a = float(np.dot(d, d))
    if a < 1e-12:
        return True
    # Parameter of closest approach of the infinite line to the origin
    t = -float(np.dot(p1, d)) / a
    t_clamped = float(np.clip(t, 0.0, 1.0))
    closest = p1 + t_clamped * d
    return float(np.linalg.norm(closest)) >= earth_radius_km - 1e-3


def elevation_deg(sat_ecef: np.ndarray, ground_ecef: np.ndarray) -> float:
    """Elevation angle of satellite as seen from ground site (degrees)."""
    los = sat_ecef - ground_ecef
    # Local up vector ≈ ground_ecef direction
    up = ground_ecef / np.linalg.norm(ground_ecef)
    cos_zenith = np.dot(los, up) / np.linalg.norm(los)
    cos_zenith = np.clip(cos_zenith, -1.0, 1.0)
    elev = 90.0 - np.degrees(np.arccos(cos_zenith))
    return float(elev)


def compute_snapshot(
    time_s: float,
    sat_positions: Dict[str, np.ndarray],   # name -> (3,) ECEF km
    ground_positions: Dict[str, np.ndarray],
    params: LinkParams,
    include_sat_sat: bool = True,
    include_ground_sat: bool = True,
) -> TopologySnapshot:
    """Build the instantaneous topology at one time step."""
    edges: List[LinkEdge] = []
    adj: Dict[str, Set[str]] = {n: set() for n in list(sat_positions) + list(ground_positions)}

    sat_names = list(sat_positions.keys())
    gnd_names = list(ground_positions.keys())

    # Satellite – satellite links
    if include_sat_sat:
        for i in range(len(sat_names)):
            for j in range(i + 1, len(sat_names)):
                u, v = sat_names[i], sat_names[j]
                p1, p2 = sat_positions[u], sat_positions[v]
                if np.any(np.isnan(p1)) or np.any(np.isnan(p2)):
                    continue
                dist = float(np.linalg.norm(p2 - p1))
                if dist > params.max_range_km:
                    continue
                if not los_clear(p1, p2):
                    continue
                delay = dist / C_LIGHT_KM_S
                edges.append(LinkEdge(u, v, delay, params.capacity_mbps, dist))
                adj[u].add(v)
                adj[v].add(u)

    # Ground – satellite links
    if include_ground_sat:
        for g in gnd_names:
            gp = ground_positions[g]
            for s in sat_names:
                sp = sat_positions[s]
                if np.any(np.isnan(sp)):
                    continue
                dist = float(np.linalg.norm(sp - gp))
                if dist > params.max_range_km:
                    continue
                elev = elevation_deg(sp, gp)
                if elev < params.min_elevation_deg:
                    continue
                if not los_clear(gp, sp):
                    continue
                delay = dist / C_LIGHT_KM_S
                edges.append(LinkEdge(g, s, delay, params.capacity_mbps, dist))
                adj[g].add(s)
                adj[s].add(g)

    return TopologySnapshot(time_s=time_s, edges=edges, adj=adj)


def build_ground_positions(sites) -> Dict[str, np.ndarray]:
    """Convert list of GroundSite objects to ECEF dict."""
    return {
        s.name: ground_site_ecef(s.lat_deg, s.lon_deg, s.alt_m)
        for s in sites
    }
