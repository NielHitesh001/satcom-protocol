"""
Engine-level tests: event ordering, reproducibility, and metric bookkeeping.

Uses a tiny synthetic scenario so the full Simulator path is exercised
without depending on real TLE geometry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from config import Scenario, load_scenario
from engine import Simulator, run_scenario
from metrics import RunMetrics


# ---------------------------------------------------------------------------
# Helpers – write a minimal scenario that only needs sat-to-sat links
# ---------------------------------------------------------------------------

MINIMAL_SCENARIO = {
    "name": "test_minimal",
    "start_time_utc": "2026-01-01T00:00:00Z",
    "duration_s": 60,
    "time_step_s": 30,
    "tle_path": "data/starlink_sample.tle",
    "tle_snapshot_id": "test-snapshot",
    "max_sats": 8,
    "protocol": "predictive",
    "seed": 7,
    "ground_sites": [],
    "link": {
        "max_range_km": 5000.0,
        "min_elevation_deg": 25.0,
        "capacity_mbps": 100.0,
        "loss_prob": 0.0,
    },
    "traffic": [
        {
            "flow_id": "f1",
            "source": "STARLINK-1008",
            "destination": "STARLINK-1020",
            "start_s": 5,
            "duration_s": 40,
            "rate_pps": 0.5,
            "packet_size_bytes": 256,
        }
    ],
}


@pytest.fixture
def scenario_file(tmp_path):
    """Write a minimal scenario YAML that points at the sample TLE."""
    # Resolve TLE relative to the package root
    pkg = Path(__file__).resolve().parent.parent
    tle = pkg / "data" / "starlink_sample.tle"
    data = dict(MINIMAL_SCENARIO)
    data["tle_path"] = str(tle)
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.dump(data))
    return path


# ---------------------------------------------------------------------------
# Reproducibility: same seed → identical metrics
# ---------------------------------------------------------------------------

def test_reproducible_with_same_seed(scenario_file, tmp_path):
    m1 = run_scenario(scenario_file, output_dir=tmp_path / "r1")
    m2 = run_scenario(scenario_file, output_dir=tmp_path / "r2")
    s1 = m1.summary_dict()
    s2 = m2.summary_dict()
    assert s1["packets_sent"] == s2["packets_sent"]
    assert s1["packets_delivered"] == s2["packets_delivered"]
    assert s1["delivery_ratio"] == s2["delivery_ratio"]
    assert s1["seed"] == 7


# ---------------------------------------------------------------------------
# Manifest and CSV are written and parseable
# ---------------------------------------------------------------------------

def test_outputs_written(scenario_file, tmp_path):
    out = tmp_path / "out"
    metrics = run_scenario(scenario_file, output_dir=out)
    assert isinstance(metrics, RunMetrics)

    csvs = list(out.glob("*_flows.csv"))
    manifests = list(out.glob("*_manifest.json"))
    assert len(csvs) == 1
    assert len(manifests) == 1

    data = json.loads(manifests[0].read_text())
    assert data["protocol"] == "predictive"
    assert data["seed"] == 7
    assert "delivery_ratio" in data
    assert data["packets_sent"] > 0


# ---------------------------------------------------------------------------
# Topology snapshots are time-ordered and cover the duration
# ---------------------------------------------------------------------------

def test_snapshot_time_ordering(scenario_file):
    scenario = load_scenario(scenario_file)
    # Resolve TLE path the same way the engine does
    tle = Path(scenario.tle_path)
    if not tle.is_absolute():
        scenario.tle_path = str((Path(scenario_file).parent / tle).resolve())

    sim = Simulator(scenario)
    times = [s.time_s for s in sim.snapshots]
    assert times == sorted(times)
    assert times[0] == 0.0
    # Last snapshot at or near duration
    assert times[-1] >= scenario.duration_s - scenario.time_step_s
    # Number of steps roughly duration / step
    expected = int(scenario.duration_s / scenario.time_step_s) + 1
    assert len(times) == expected


# ---------------------------------------------------------------------------
# Packets are counted: sent == delivered + dropped (for the predictive path)
# ---------------------------------------------------------------------------

def test_packet_conservation_predictive(scenario_file, tmp_path):
    metrics = run_scenario(scenario_file, output_dir=tmp_path)
    total_sent = sum(f.sent for f in metrics.flows.values())
    total_del = sum(f.delivered for f in metrics.flows.values())
    total_drop = sum(f.dropped for f in metrics.flows.values())
    # Every injected packet is either delivered or dropped
    assert total_sent == total_del + total_drop
    assert total_sent > 0
