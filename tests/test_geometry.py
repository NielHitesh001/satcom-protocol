"""Unit tests for coordinate conversion, LOS and elevation checks."""

import numpy as np
import pytest

from orbit import ecef_to_geodetic, ground_site_ecef, WGS84_A
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
    # Satellite almost directly above a ground point (radial)
    g = ground_site_ecef(0.0, 0.0, 0.0)
    s = g * ((WGS84_A + 550.0) / WGS84_A)
    assert los_clear(g, s) is True


def test_los_blocked_through_earth():
    g1 = ground_site_ecef(0.0, 0.0, 0.0)
    g2 = ground_site_ecef(0.0, 180.0, 0.0)  # opposite side
    assert los_clear(g1, g2) is False


def test_elevation_overhead():
    g = ground_site_ecef(0.0, 0.0, 0.0)
    # Place satellite radially outward (true overhead)
    s = g * ((WGS84_A + 550.0) / WGS84_A)
    elev = elevation_deg(s, g)
    assert elev > 85.0
