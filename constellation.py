"""
constellation.py – Synthetic and profile-based LEO constellation generators.

Synthetic configurations are clearly labeled. They are NOT real Starlink TLEs.
Real TLE loading remains via orbit.load_tles().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from orbit import SatelliteState, load_tles


@dataclass
class ConstellationProfile:
    """Declarative constellation configuration."""
    name: str
    source: str  # "synthetic" | "tle_file"
    n_sats: int
    n_planes: int = 1
    inclination_deg: float = 53.0
    altitude_km: float = 550.0
    eccentricity: float = 0.0001
    max_range_km: float = 3000.0
    tle_path: Optional[str] = None
    tle_provenance: Dict[str, str] = field(default_factory=dict)
    notes: str = ""


# Earth radius for mean motion (km)
_R_EARTH = 6378.137
_MU = 398600.4418  # km^3/s^2


def _mean_motion_rev_per_day(altitude_km: float) -> float:
    a = _R_EARTH + altitude_km
    n_rad_s = np.sqrt(_MU / a**3)
    return float(n_rad_s * 86400.0 / (2.0 * np.pi))


def _tle_checksum(line: str) -> int:
    s = 0
    for ch in line[:68]:
        if ch.isdigit():
            s += int(ch)
        elif ch == "-":
            s += 1
    return s % 10


def generate_walker_tles(
    n_sats: int,
    n_planes: int = 6,
    inclination_deg: float = 53.0,
    altitude_km: float = 550.0,
    epoch_day: float = 26001.0,  # YYDDD.fraction style for line 1 field
    name_prefix: str = "SYN-LEO",
) -> str:
    """
    Generate synthetic 3-line TLE blocks for a Walker-like constellation.

    These are **synthetic**. They are suitable for scaling studies, not for
    claiming operational constellation fidelity.
    """
    if n_planes < 1:
        n_planes = 1
    per_plane = max(1, n_sats // n_planes)
    actual_n = per_plane * n_planes
    n_mm = _mean_motion_rev_per_day(altitude_km)
    lines: List[str] = []
    norad = 90000
    for p in range(n_planes):
        raan = (360.0 * p) / n_planes
        for k in range(per_plane):
            mean_anom = (360.0 * k) / per_plane + (360.0 * p) / (n_planes * per_plane)
            norad += 1
            name = f"{name_prefix}-{norad}"
            # Line 1
            l1_body = (
                f"1 {norad:05d}U 00000A   {epoch_day:014.8f}  .00000000  "
                f"00000-0  00000-0 0  "
            )
            l1 = l1_body + f"{_tle_checksum(l1_body + '0')}"
            # Line 2
            l2_body = (
                f"2 {norad:05d} {inclination_deg:8.4f} {raan:8.4f} "
                f"0001000  90.0000 {mean_anom:8.4f} {n_mm:11.8f}"
            )
            # pad/truncate to 68 chars before checksum
            l2_body = l2_body[:68].ljust(68)
            l2 = l2_body + str(_tle_checksum(l2_body))
            lines.append(name)
            lines.append(l1)
            lines.append(l2)
    return "\n".join(lines) + "\n"


def write_walker_tle_file(
    path: str | Path,
    n_sats: int,
    n_planes: int = 6,
    **kwargs,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = generate_walker_tles(n_sats, n_planes=n_planes, **kwargs)
    path.write_text(text, encoding="utf-8")
    return path


def load_profile(profile: ConstellationProfile) -> Dict[str, SatelliteState]:
    """Load satellites for a profile (TLE file or generate synthetic then load)."""
    if profile.source == "tle_file":
        if not profile.tle_path:
            raise ValueError("tle_file profile requires tle_path")
        return load_tles(profile.tle_path, max_sats=profile.n_sats)

    # synthetic: write temp TLE then load
    tmp = Path("data") / f"_synthetic_{profile.name}_{profile.n_sats}.tle"
    write_walker_tle_file(
        tmp,
        n_sats=profile.n_sats,
        n_planes=profile.n_planes,
        inclination_deg=profile.inclination_deg,
        altitude_km=profile.altitude_km,
    )
    return load_tles(tmp, max_sats=profile.n_sats)


PRESETS: Dict[str, ConstellationProfile] = {
    "sample_tle": ConstellationProfile(
        name="sample_tle",
        source="tle_file",
        n_sats=30,
        tle_path="data/starlink_sample.tle",
        tle_provenance={
            "type": "sample_synthetic_starlink_style",
            "note": "Small sample in repo; NOT full Starlink",
        },
        notes="Repository sample TLE set",
    ),
    "walker_50": ConstellationProfile(
        name="walker_50",
        source="synthetic",
        n_sats=50,
        n_planes=5,
        notes="Synthetic Walker-like; not operational data",
    ),
    "walker_100": ConstellationProfile(
        name="walker_100",
        source="synthetic",
        n_sats=100,
        n_planes=10,
        notes="Synthetic Walker-like; not operational data",
    ),
    "walker_200": ConstellationProfile(
        name="walker_200",
        source="synthetic",
        n_sats=200,
        n_planes=10,
        notes="Synthetic Walker-like; not operational data",
    ),
}
