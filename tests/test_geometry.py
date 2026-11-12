"""Unit tests for coordinate conversion, LOS and elevation checks."""

import numpy as np
import pytest

from orbit import ecef_to_geodetic, ground_site_ecef, WGS84_A, teme_to_ecef, gmst_rad
from links import los_clear, elevation_deg


def test_ground_site_ecef_equator():
    pos = ground_site_ecef(0.0, 0.0, 0.0)
    assert abs(pos[0] - WGS84_A) < 1.0
    assert abs(pos[1]) < 1.0
    assert abs(pos[2]) < 1.0


def test_ecef_to_geodetic_roundtrip():
    lat, lon, alt = 12.97, 77.59, 0.9
    ecef = ground_site_ecef(lat, lon, alt * 1000)
    lat2, lon2, alt2 = ecef_to_geodetic(*ecef)
    assert abs(lat - lat2) < 0.01
    assert abs(lon - lon2) < 0.01
    assert abs(alt - alt2) < 0.1


def test_los_clear_overhead():
    g = ground_site_ecef(0.0, 0.0, 0.0)
    s = g * ((WGS84_A + 550.0) / WGS84_A)
    assert los_clear(g, s) is True


def test_los_blocked_through_earth():
    g1 = ground_site_ecef(0.0, 0.0, 0.0)
    g2 = ground_site_ecef(0.0, 180.0, 0.0)
    assert los_clear(g1, g2) is False


def test_elevation_overhead():
    g = ground_site_ecef(0.0, 0.0, 0.0)
    s = g * ((WGS84_A + 550.0) / WGS84_A)
    elev = elevation_deg(s, g)
    assert elev > 85.0


def test_teme_to_ecef_rotation():
    """GMST rotation must change the vector; magnitude is preserved."""
    from sgp4.api import jday
    r_teme = np.array([7000.0, 0.0, 0.0])
    jd, fr = jday(2026, 1, 1, 0, 0, 0)
    r_ecef = teme_to_ecef(r_teme, jd, fr)
    assert abs(np.linalg.norm(r_ecef) - 7000.0) < 1e-6
    # At this epoch GMST is ~100 deg → large displacement from TEME
    assert np.linalg.norm(r_ecef - r_teme) > 1000.0


def test_gmst_known_epoch():
    """GMST at J2000 is near 280 deg (standard reference)."""
    g = np.degrees(gmst_rad(2451545.0, 0.0)) % 360
    assert 279.0 < g < 282.0
