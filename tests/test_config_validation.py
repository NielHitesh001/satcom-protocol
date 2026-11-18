"""Validation tests for scenario inputs that would corrupt a simulation."""

from __future__ import annotations

import copy

import pytest

from config import Scenario


def _scenario() -> dict:
    return {
        "name": "valid",
        "start_time_utc": "2026-01-01T00:00:00Z",
        "duration_s": 60,
        "time_step_s": 10,
        "tle_path": "data/starlink_sample.tle",
        "ground_sites": [],
        "protocol": "aodv",
        "link": {"max_range_km": 5000, "capacity_mbps": 100, "loss_prob": 0, "queue_limit": 4},
        "traffic": [
            {
                "flow_id": "f1",
                "source": "SAT-A",
                "destination": "SAT-B",
                "start_s": 0,
                "duration_s": 10,
                "rate_pps": 1,
                "packet_size_bytes": 256,
            }
        ],
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(time_step_s=0), "time_step_s"),
        (lambda d: d["link"].update(capacity_mbps=0), "capacity_mbps"),
        (lambda d: d["link"].update(loss_prob=1.1), "loss_prob"),
        (lambda d: d["link"].update(queue_limit=-1), "queue_limit"),
        (lambda d: d["traffic"][0].update(rate_pps=0), "rate_pps"),
        (lambda d: d["traffic"][0].update(packet_size_bytes=0), "packet_size_bytes"),
    ],
)
def test_rejects_invalid_simulation_values(mutate, message):
    data = _scenario()
    mutate(data)
    with pytest.raises(ValueError, match=message):
        Scenario.from_dict(data)


def test_rejects_duplicate_flow_ids():
    data = _scenario()
    duplicate = copy.deepcopy(data["traffic"][0])
    duplicate["destination"] = "SAT-C"
    data["traffic"].append(duplicate)
    with pytest.raises(ValueError, match="flow_id"):
        Scenario.from_dict(data)


def test_accepts_supported_protocol_aliases():
    data = _scenario()
    data["protocol"] = "oracle"
    assert Scenario.from_dict(data).protocol == "oracle"
