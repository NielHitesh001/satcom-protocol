"""Phase 4: synthetic constellation load, spatial equivalence at larger N, conservation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from config import LinkParams
from constellation import write_walker_tle_file
from links import compute_snapshot
from orbit import load_tles, propagate_positions


def test_walker_tle_loads_and_propagates(tmp_path):
    path = write_walker_tle_file(tmp_path / "w.tle", n_sats=24, n_planes=4)
    sats = load_tles(path, max_sats=24)
    assert len(sats) == 24
    times = [datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=10 * i) for i in range(3)]
    pos = propagate_positions(sats, times)
    assert len(pos) == 24
    # At least some finite positions
    finite = sum(1 for a in pos.values() if np.isfinite(a).all())
    assert finite == 24


def test_spatial_brute_equivalence_n80():
    path = write_walker_tle_file(Path("data/_eq80.tle"), n_sats=80, n_planes=8)
    sats = load_tles(path, max_sats=80)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    series = propagate_positions(sats, [t0])
    pos = {n: series[n][0] for n in series}
    params = LinkParams(max_range_km=4000.0)
    b = compute_snapshot(0.0, pos, {}, params, use_spatial_index=False)
    s = compute_snapshot(0.0, pos, {}, params, use_spatial_index=True)
    be = {frozenset((e.u, e.v)) for e in b.edges}
    se = {frozenset((e.u, e.v)) for e in s.edges}
    assert be == se


def test_spatial_scales_without_error_n200():
    path = write_walker_tle_file(Path("data/_sc200.tle"), n_sats=200, n_planes=10)
    sats = load_tles(path, max_sats=200)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    series = propagate_positions(sats, [t0])
    pos = {n: series[n][0] for n in series}
    params = LinkParams(max_range_km=3000.0)
    snap = compute_snapshot(0.0, pos, {}, params, use_spatial_index=True)
    assert snap.time_s == 0.0
    assert len(snap.adj) >= 200
