"""
Phase 3: spatial index must produce identical topology to brute force.
"""

from __future__ import annotations

import numpy as np

from config import LinkParams
from links import compute_snapshot


def _random_sats(n: int, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    # LEO-like radii ~6700–7200 km
    positions = {}
    for i in range(n):
        r = 6900.0 + rng.uniform(-200, 200)
        theta = rng.uniform(0, 2 * np.pi)
        phi = rng.uniform(0, np.pi)
        positions[f"S{i}"] = np.array([
            r * np.sin(phi) * np.cos(theta),
            r * np.sin(phi) * np.sin(theta),
            r * np.cos(phi),
        ])
    return positions


def _edge_set(snap) -> set:
    return frozenset(
        frozenset((e.u, e.v)) for e in snap.edges
    )


def test_spatial_matches_bruteforce_small():
    params = LinkParams(max_range_km=3000.0)
    sats = _random_sats(25, seed=1)
    brute = compute_snapshot(0.0, sats, {}, params, use_spatial_index=False)
    spatial = compute_snapshot(0.0, sats, {}, params, use_spatial_index=True)
    assert _edge_set(brute) == _edge_set(spatial)
    assert len(brute.edges) == len(spatial.edges)


def test_spatial_matches_bruteforce_medium():
    params = LinkParams(max_range_km=2500.0)
    sats = _random_sats(80, seed=42)
    brute = compute_snapshot(0.0, sats, {}, params, use_spatial_index=False)
    spatial = compute_snapshot(0.0, sats, {}, params, use_spatial_index=True)
    assert _edge_set(brute) == _edge_set(spatial)


def test_spatial_auto_threshold():
    """N <= threshold uses brute path; result still valid."""
    params = LinkParams(max_range_km=4000.0)
    sats = _random_sats(10, seed=3)
    snap = compute_snapshot(0.0, sats, {}, params, use_spatial_index=None)
    assert snap.time_s == 0.0
    # All nodes present in adj
    for name in sats:
        assert name in snap.adj


def test_spatial_distances_match():
    params = LinkParams(max_range_km=3500.0)
    sats = _random_sats(40, seed=7)
    brute = compute_snapshot(0.0, sats, {}, params, use_spatial_index=False)
    spatial = compute_snapshot(0.0, sats, {}, params, use_spatial_index=True)
    brute_d = {(min(e.u, e.v), max(e.u, e.v)): e.distance_km for e in brute.edges}
    spatial_d = {(min(e.u, e.v), max(e.u, e.v)): e.distance_km for e in spatial.edges}
    assert brute_d.keys() == spatial_d.keys()
    for k in brute_d:
        assert abs(brute_d[k] - spatial_d[k]) < 1e-9
