"""
connectivity.py – Graph components and connectivity-aware traffic endpoints.

Connectivity is evaluated on a TopologySnapshot (actual link graph).
Selecting endpoints in the same component does NOT guarantee delivery:
routes may break after injection, buffers may residual, loss may apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from links import TopologySnapshot


@dataclass
class EndpointChoice:
    source: str
    destination: str
    component_size: int
    component_id: int
    n_components: int
    mode: str  # "fixed" | "connectivity_aware"
    injection_time_s: float
    notes: str = ""


def connected_components(adj: Dict[str, Set[str]]) -> List[List[str]]:
    """Return list of components (each a sorted list of node names)."""
    seen: Set[str] = set()
    comps: List[List[str]] = []
    for start in adj:
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        comp = []
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(sorted(comp))
    comps.sort(key=len, reverse=True)
    return comps


def select_endpoints_connectivity_aware(
    snap: TopologySnapshot,
    rng: np.random.Generator,
    min_component_size: int = 2,
    injection_time_s: float = 0.0,
) -> Optional[EndpointChoice]:
    """
    Pick distinct src/dst uniformly from a random eligible connected component.

    Eligible: component size >= min_component_size.
    Prefers larger components by first filtering, then uniform among eligible nodes.
    Returns None if no eligible component exists.
    """
    comps = connected_components(snap.adj)
    eligible = [c for c in comps if len(c) >= min_component_size]
    if not eligible:
        return None
    # Weight by component size so larger components are more likely
    weights = np.array([len(c) for c in eligible], dtype=np.float64)
    weights /= weights.sum()
    idx = int(rng.choice(len(eligible), p=weights))
    comp = eligible[idx]
    if len(comp) < 2:
        return None
    pair = rng.choice(comp, size=2, replace=False)
    src, dst = str(pair[0]), str(pair[1])
    return EndpointChoice(
        source=src,
        destination=dst,
        component_size=len(comp),
        component_id=idx,
        n_components=len(comps),
        mode="connectivity_aware",
        injection_time_s=injection_time_s,
        notes="Endpoints co-located in one component at injection topology epoch",
    )


def fixed_endpoints(
    source: str,
    destination: str,
    snap: Optional[TopologySnapshot] = None,
    injection_time_s: float = 0.0,
) -> EndpointChoice:
    """Record fixed endpoints; optionally annotate component membership if snap given."""
    comp_size = 0
    n_comp = 0
    same = False
    if snap is not None:
        comps = connected_components(snap.adj)
        n_comp = len(comps)
        for c in comps:
            if source in c:
                comp_size = len(c)
                same = destination in c
                break
    return EndpointChoice(
        source=source,
        destination=destination,
        component_size=comp_size,
        component_id=-1,
        n_components=n_comp,
        mode="fixed",
        injection_time_s=injection_time_s,
        notes=("same_component" if same else "may_be_disconnected") if snap else "fixed_no_topology_check",
    )
