"""Benchmark sat–sat topology: brute force vs spatial hash."""
from __future__ import annotations
import time
import numpy as np
from config import LinkParams
from links import compute_snapshot

def random_sats(n: int, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
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

def main() -> None:
    params = LinkParams(max_range_km=3000.0)
    print(f"{'N':>6} {'brute_ms':>10} {'spatial_ms':>12} {'edges':>8} {'speedup':>8}")
    for n in (20, 50, 100, 200, 400):
        sats = random_sats(n, seed=n)
        compute_snapshot(0.0, sats, {}, params, use_spatial_index=False)
        compute_snapshot(0.0, sats, {}, params, use_spatial_index=True)
        t0 = time.perf_counter()
        for _ in range(5):
            b = compute_snapshot(0.0, sats, {}, params, use_spatial_index=False)
        brute_ms = (time.perf_counter() - t0) / 5 * 1000
        t0 = time.perf_counter()
        for _ in range(5):
            s = compute_snapshot(0.0, sats, {}, params, use_spatial_index=True)
        spatial_ms = (time.perf_counter() - t0) / 5 * 1000
        assert len(b.edges) == len(s.edges)
        speedup = brute_ms / spatial_ms if spatial_ms > 0 else float("inf")
        print(f"{n:6d} {brute_ms:10.2f} {spatial_ms:12.2f} {len(b.edges):8d} {speedup:8.2f}x")

if __name__ == "__main__":
    main()
