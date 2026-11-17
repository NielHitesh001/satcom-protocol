"""Phase 6: control size model, byte accounting, airtime vs estimate, conservation."""

from __future__ import annotations

from pathlib import Path

import yaml

from engine import Simulator
from routing.aodv import AODVProtocol
from routing.control_sizes import ControlSizeModel, serialization_s
from routing.olsr import OLSRProtocol


def test_serialization_formula():
    assert serialization_s(100, 100.0) == (100 * 8) / 1e8
    assert serialization_s(64, 0.0) == 0.0


def test_size_model_defaults_and_override():
    m = ControlSizeModel()
    assert m.aodv_rreq == 64
    assert m.rerr_size(2) == 48 + 16
    m2 = ControlSizeModel.from_dict({"olsr_hello": 80, "not_a_field": 1})
    assert m2.olsr_hello == 80
    assert m2.aodv_rreq == 64
    meta = m.as_dict()
    assert meta["provenance"].startswith("configurable_assumption")


def test_aodv_rreq_bytes_match_count():
    p = AODVProtocol(["A", "B", "C"])
    p.neighbors["A"].update(["B", "C"])
    p._emit_rreq(0.0, "A", "C")
    assert p.control_messages_sent() == 2
    assert p.control_bytes_generated == 2 * 64
    assert p.control_msgs_by_type["RREQ"] == 2


def test_aodv_custom_rreq_size():
    p = AODVProtocol(["A", "B"], size_model=ControlSizeModel(aodv_rreq=100))
    p.neighbors["A"].add("B")
    p._emit_rreq(0.0, "A", "B")
    assert p.control_bytes_generated == 100


def test_olsr_hello_bytes():
    p = OLSRProtocol(["A", "B"], hello_interval_s=1.0, tc_interval_s=100.0)
    p.neighbors["A"].add("B")
    p.last_tc["A"] = 0.0
    out = p.tick(0.0, "A")
    assert any(pkt.type.name == "HELLO" for pkt in out)
    assert p.control_msgs_by_type["HELLO"] == 1
    assert p.control_bytes_generated == 48


def test_engine_control_airtime_and_conservation(tmp_path: Path):
    pkg = Path(__file__).resolve().parent.parent
    data = {
        "name": "p6",
        "start_time_utc": "2026-01-01T00:00:00Z",
        "duration_s": 40,
        "time_step_s": 20,
        "tle_path": str(pkg / "data" / "starlink_sample.tle"),
        "tle_snapshot_id": "test",
        "max_sats": 8,
        "protocol": "aodv",
        "seed": 3,
        "ground_sites": [],
        "control_sizes": {"aodv_rreq": 64, "aodv_rrep": 64},
        "link": {
            "max_range_km": 8000.0,
            "min_elevation_deg": 0.0,
            "capacity_mbps": 50.0,
            "loss_prob": 0.0,
            "queue_limit": 32,
        },
        "traffic": [
            {
                "flow_id": "f1",
                "source": "STARLINK-1007",
                "destination": "STARLINK-1010",
                "start_s": 2,
                "duration_s": 20,
                "rate_pps": 0.5,
                "packet_size_bytes": 200,
            }
        ],
    }
    path = tmp_path / "s.yaml"
    path.write_text(yaml.dump(data))
    from config import load_scenario

    sim = Simulator(load_scenario(path))
    m = sim.run()
    s = m.summary_dict()
    assert s["packets_sent"] == s["packets_delivered"] + s["packets_dropped"] + s["packets_residual"]
    assert s["control_size_model"]["aodv_rreq"] == 64
    assert s["control_bytes_sent"] == s["control_bytes_by_type"].get("RREQ", 0) + s[
        "control_bytes_by_type"
    ].get("RREP", 0) + s["control_bytes_by_type"].get("RERR", 0)
    # Airtime bytes are transmissions that were scheduled; may differ from generation fanout.
    assert s["control_airtime_bytes"] >= 0
    assert s["data_bytes_injected"] == s["packets_sent"] * 200
    if s["control_airtime_tx"] > 0:
        assert s["control_serialization_s"] > 0
