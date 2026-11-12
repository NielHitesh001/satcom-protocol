"""Topology constraints that prevent unrealistically dense ISL graphs."""

from __future__ import annotations

import numpy as np

import links
from config import LinkParams


def test_isl_degree_cap_limits_each_satellite(monkeypatch):
    monkeypatch.setattr(links, "los_clear", lambda *_: True)
    positions = {
        "A": np.array([0.0, 0.0, 0.0]),
        "B": np.array([1.0, 0.0, 0.0]),
        "C": np.array([0.0, 1.0, 0.0]),
        "D": np.array([1.0, 1.0, 0.0]),
    }
    snap = links.compute_snapshot(
        0.0,
        positions,
        {},
        LinkParams(max_range_km=10.0, max_isl_degree=1),
        use_spatial_index=False,
    )

    assert all(len(neighbors) <= 1 for neighbors in snap.adj.values())
    assert len(snap.edges) == 2
