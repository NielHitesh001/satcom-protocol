"""Synthetic and profile-based LEO constellation generators. Synthetic != operational Starlink."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional
import numpy as np
from orbit import SatelliteState, load_tles

_R_EARTH = 6378.137
_MU = 398600.4418

@dataclass
class ConstellationProfile:
    name: str
    source: str
    n_sats: int
    n_planes: int = 1
    inclination_deg: float = 53.0
    altitude_km: float = 550.0
    eccentricity: float = 0.0001
    max_range_km: float = 3000.0
    tle_path: Optional[str] = None
    tle_provenance: Dict[str, str] = field(default_factory=dict)
    notes: str = ""

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
    n_sats: int, n_planes: int = 6, inclination_deg: float = 53.0,
    altitude_km: float = 550.0, epoch_day: float = 26001.0, name_prefix: str = "SYN-LEO",
) -> str:
    if n_planes < 1:
        n_planes = 1
    per_plane = max(1, n_sats // n_planes)
    n_mm = _mean_motion_rev_per_day(altitude_km)
    lines = []
    norad = 90000
    for p in range(n_planes):
        raan = (360.0 * p) / n_planes
        for k in range(per_plane):
            mean_anom = (360.0 * k) / per_plane + (360.0 * p) / (n_planes * per_plane)
            norad += 1
            name = f"{name_prefix}-{norad}"
            l1_body = f"1 {norad:05d}U 00000A   {epoch_day:014.8f}  .00000000  00000-0  00000-0 0  "
            l1 = l1_body + f"{_tle_checksum(l1_body + '0')}"
            l2_body = (f"2 {norad:05d} {inclination_deg:8.4f} {raan:8.4f} "
                       f"0001000  90.0000 {mean_anom:8.4f} {n_mm:11.8f}")[:68].ljust(68)
            l2 = l2_body + str(_tle_checksum(l2_body))
            lines.extend([name, l1, l2])
    return "\n".join(lines) + "\n"

def write_walker_tle_file(path, n_sats: int, n_planes: int = 6, **kwargs) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_walker_tles(n_sats, n_planes=n_planes, **kwargs), encoding="utf-8")
    return path

def load_profile(profile: ConstellationProfile):
    if profile.source == "tle_file":
        return load_tles(profile.tle_path, max_sats=profile.n_sats)
    tmp = Path("data") / f"_synthetic_{profile.name}_{profile.n_sats}.tle"
    write_walker_tle_file(tmp, n_sats=profile.n_sats, n_planes=profile.n_planes,
                          inclination_deg=profile.inclination_deg, altitude_km=profile.altitude_km)
    return load_tles(tmp, max_sats=profile.n_sats)
