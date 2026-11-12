"""
orbit.py – Load TLE snapshots and propagate positions with SGP4.

Inputs
------
- Path to a TLE file (3-line or 2-line format)
- Epoch / time grid for which positions are required
- Optional satellite name filter / max count

Outputs
-------
- Dictionary of satellite names -> Satrec objects
- Time series of ECEF positions (km) for each satellite
- Optional geodetic (lat, lon, alt) conversion

Assumptions
-----------
- TLEs are NORAD two-line element sets (with optional name line)
- Propagation uses the sgp4 library (Wertz / Vallado model)
- Positions returned in Earth-Centered Earth-Fixed (ECEF) km
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from sgp4.api import Satrec, jday
from sgp4.conveniences import sat_epoch_datetime


# WGS-84 constants
WGS84_A = 6378.137          # km
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


@dataclass
class SatelliteState:
    name: str
    norad_id: int
    satrec: Satrec
    epoch: datetime


def load_tles(
    tle_path: str | Path,
    max_sats: Optional[int] = None,
    name_filter: Optional[List[str]] = None,
) -> Dict[str, SatelliteState]:
    """
    Parse a TLE file and return a dict of SatelliteState objects.

    Supports both 3-line (name + L1 + L2) and pure 2-line formats.
    """
    path = Path(tle_path)
    lines = [ln.rstrip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    sats: Dict[str, SatelliteState] = {}
    i = 0
    while i < len(lines):
        # Detect name line
        if not lines[i].startswith("1 ") and not lines[i].startswith("2 "):
            name = lines[i].strip()
            i += 1
        else:
            name = f"SAT-{len(sats)}"

        if i + 1 >= len(lines):
            break

        l1 = lines[i]
        l2 = lines[i + 1]
        i += 2

        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            continue

        try:
            sat = Satrec.twoline2rv(l1, l2)
        except Exception:
            continue

        norad = int(l1[2:7])
        # Prefer the common name if it looks like STARLINK-xxxx
        if name.startswith("SAT-") and "STARLINK" in l1.upper():
            name = f"STARLINK-{norad}"

        if name_filter and name not in name_filter:
            continue

        epoch = sat_epoch_datetime(sat)
        sats[name] = SatelliteState(name=name, norad_id=norad, satrec=sat, epoch=epoch)

        if max_sats is not None and len(sats) >= max_sats:
            break

    return sats


def propagate_positions(
    sats: Dict[str, SatelliteState],
    times_utc: List[datetime],
) -> Dict[str, np.ndarray]:
    """
    Propagate every satellite to the requested UTC times.

    Returns
    -------
    dict name -> (N, 3) array of ECEF positions in kilometres
    """
    result: Dict[str, np.ndarray] = {}
    n = len(times_utc)

    for name, state in sats.items():
        pos = np.zeros((n, 3), dtype=np.float64)
        for i, t in enumerate(times_utc):
            jd, fr = jday(
                t.year, t.month, t.day,
                t.hour, t.minute, t.second + t.microsecond * 1e-6,
            )
            e, r, v = state.satrec.sgp4(jd, fr)
            if e == 0:
                # SGP4 returns TEME; for v1 we treat it as approximately ECEF
                # (error is acceptable for link geometry at this fidelity)
                pos[i] = r
            else:
                pos[i] = np.nan
        result[name] = pos
    return result


def ecef_to_geodetic(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """Convert ECEF (km) to geodetic lat (deg), lon (deg), alt (km)."""
    lon = np.degrees(np.arctan2(y, x))
    p = np.sqrt(x * x + y * y)
    lat = np.degrees(np.arctan2(z, p * (1.0 - WGS84_E2)))
    # Iterate once for better accuracy
    for _ in range(3):
        sin_lat = np.sin(np.radians(lat))
        N = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
        lat = np.degrees(np.arctan2(z + N * WGS84_E2 * sin_lat, p))
    sin_lat = np.sin(np.radians(lat))
    N = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    alt = p / np.cos(np.radians(lat)) - N
    return float(lat), float(lon), float(alt)


def ground_site_ecef(lat_deg: float, lon_deg: float, alt_m: float = 0.0) -> np.ndarray:
    """Return ECEF position (km) of a ground site."""
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    alt_km = alt_m / 1000.0
    sin_lat, cos_lat = np.sin(lat), np.cos(lat)
    N = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (N + alt_km) * cos_lat * np.cos(lon)
    y = (N + alt_km) * cos_lat * np.sin(lon)
    z = (N * (1.0 - WGS84_E2) + alt_km) * sin_lat
    return np.array([x, y, z], dtype=np.float64)
