"""
links.py – Compute time-varying satellite / ground topology.

A link exists when:
  1. Line-of-sight is clear (segment does not intersect Earth)
  2. Range <= max_range_km
  3. For ground↔sat: elevation >= min_elevation_deg

Sat–sat candidate pairs use a 3-D spatial hash grid (cell size = max_range_km)
so complexity is O(N · k) instead of O(N²) for sparse LEO graphs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from config import LinkParams
from orbit import WGS84_A, ground_site_ecef


C_LIGHT_KM_S = 299792.458  # km/s
_SPATIAL_THRESHOLD = 20


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
    adj: Dict[str, Set[str]]


def los_clear(p1: np.ndarray, p2: np.ndarray, earth_radius_km: float = WGS84_A) -> bool:
    d = p2 - p1
    a = float(np.dot(d, d))
    if a < 1e-12:
        return True
    t = -float(np.dot(p1, d)) / a
    t_clamped = float(np.clip(t, 0.0, 1.0))
    closest = p1 + t_clamped * d
    return float(np.linalg.norm(closest)) >= earth_radius_km - 1e-3


def elevation_deg(sat_ecef: np.ndarray, ground_ecef: np.ndarray) -> float:
    los = sat_ecef - ground_ecef
    up = ground_ecef / np.linalg.norm(ground_ecef)
    cos_zenith = np.dot(los, up) / np.linalg.norm(los)
    cos_zenith = np.clip(cos_zenith, -1.0, 1.0)
    elev = 90.0 - np.degrees(np.arccos(cos_zenith))
    return float(elev)


def _cell_key(pos: np.ndarray, cell_size: float) -> Tuple[int, int, int]:
    return (
        int(np.floor(pos[0] / cell_size)),
        int(np.floor(pos[1] / cell_size)),
        int(np.floor(pos[2] / cell_size)),
    )


def _candidate_pairs_spatial(
    sat_names: List[str],
    sat_positions: Dict[str, np.ndarray],
    max_range_km: float,
) -> List[Tuple[str, str]]:
    cell_size = max(max_range_km, 1.0)
    grid: Dict[Tuple[int, int, int], List[str]] = defaultdict(list)
    valid_names: List[str] = []
    for name in sat_names:
        p = sat_positions[name]
        if np.any(np.isnan(p)):
            continue
        grid[_cell_key(p, cell_size)].append(name)
        valid_names.append(name)
    pairs: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for name in valid_names:
        p = sat_positions[name]
        cx, cy, cz = _cell_key(p, cell_size)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other in grid.get((cx + dx, cy + dy, cz + dz), []):
                        if other == name:
                            continue
                        a, b = (name, other) if name < other else (other, name)
                        key = (a, b)
                        if key in seen:
                            continue
                        seen.add(key)
                        pairs.append(key)
    return pairs


def _candidate_pairs_bruteforce(
    sat_names: List[str],
    sat_positions: Dict[str, np.ndarray],
) -> List[Tuple[str, str]]:
    pairs = []
    for i in range(len(sat_names)):
        for j in range(i + 1, len(sat_names)):
            u, v = sat_names[i], sat_names[j]
            if np.any(np.isnan(sat_positions[u])) or np.any(np.isnan(sat_positions[v])):
                continue
            pairs.append((u, v))
    return pairs


def compute_snapshot(
    time_s: float,
    sat_positions: Dict[str, np.ndarray],
    ground_positions: Dict[str, np.ndarray],
    params: LinkParams,
    include_sat_sat: bool = True,
    include_ground_sat: bool = True,
    use_spatial_index: Optional[bool] = None,
) -> TopologySnapshot:
    edges: List[LinkEdge] = []
    adj: Dict[str, Set[str]] = {
        n: set() for n in list(sat_positions) + list(ground_positions)
    }
    sat_names = list(sat_positions.keys())
    gnd_names = list(ground_positions.keys())

    if include_sat_sat and sat_names:
        n = len(sat_names)
        if use_spatial_index is None:
            use_spatial = n > _SPATIAL_THRESHOLD
        else:
            use_spatial = use_spatial_index
        if use_spatial:
            pairs = _candidate_pairs_spatial(sat_names, sat_positions, params.max_range_km)
        else:
            pairs = _candidate_pairs_bruteforce(sat_names, sat_positions)
        candidates: List[Tuple[float, str, str]] = []
        for u, v in pairs:
            p1, p2 = sat_positions[u], sat_positions[v]
            dist = float(np.linalg.norm(p2 - p1))
            if dist <= params.max_range_km and los_clear(p1, p2):
                candidates.append((dist, u, v))

        # A satellite has a finite number of steerable ISL terminals.  When a
        # degree cap is configured, retain the shortest feasible links first.
        degree: Dict[str, int] = defaultdict(int)
        degree_cap = params.max_isl_degree
        for dist, u, v in sorted(candidates):
            if degree_cap is not None and (degree[u] >= degree_cap or degree[v] >= degree_cap):
                continue
            delay = dist / C_LIGHT_KM_S
            edges.append(LinkEdge(u, v, delay, params.capacity_mbps, dist))
            adj[u].add(v)
            adj[v].add(u)
            degree[u] += 1
            degree[v] += 1

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
    return {
        s.name: ground_site_ecef(s.lat_deg, s.lon_deg, s.alt_m)
        for s in sites
    }
