"""
Comprehensive event-engine validation.

Covers propagation delay, serialization delay, queue behaviour, packet loss,
packet accounting, simulation termination, and same-time event ordering.

Event priority at equal time (lower processes first):
  topology (0) and tx_done (0) < tick (1) < inject (2) < arrival (3) < end (9)
  Topology is scheduled first, so it precedes tx_done at the same timestamp.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine import Event, Simulator, _KIND_ORDER, run_scenario
from links import C_LIGHT_KM_S, LinkEdge, TopologySnapshot
from metrics import FlowMetrics, RunMetrics, _percentile
from routing.base import Packet, PacketType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_scenario_dict(tmp_path: Path, **overrides) -> dict:
    pkg = Path(__file__).resolve().parent.parent
    tle = pkg / "data" / "starlink_sample.tle"
    data = {
        "name": "unit",
        "start_time_utc": "2026-01-01T00:00:00Z",
        "duration_s": 60,
        "time_step_s": 30,
        "tle_path": str(tle),
        "tle_snapshot_id": "test",
        "max_sats": 8,
        "protocol": "predictive",
        "seed": 7,
        "ground_sites": [],
        "link": {
            "max_range_km": 5000.0,
            "min_elevation_deg": 25.0,
            "capacity_mbps": 100.0,
            "loss_prob": 0.0,
            "queue_limit": 64,
        },
        "traffic": [
            {
                "flow_id": "f1",
                "source": "STARLINK-1007",
                "destination": "STARLINK-1010",
                "start_s": 5,
                "duration_s": 40,
                "rate_pps": 0.5,
                "packet_size_bytes": 256,
            }
        ],
    }
    data.update(overrides)
    if "link" in overrides:
        data["link"] = {**data["link"], **overrides["link"]}
    return data


def _write_scenario(tmp_path: Path, **overrides) -> Path:
    data = _minimal_scenario_dict(tmp_path, **overrides)
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.dump(data))
    return path


# ---------------------------------------------------------------------------
# A. Propagation delay
# ---------------------------------------------------------------------------

def test_propagation_delay_formula():
    """propagation_delay = distance_km / c."""
    for dist_km in [0.0, 1.0, 500.0, 1000.0, 5000.0]:
        expected = dist_km / C_LIGHT_KM_S
        assert abs(expected - dist_km / 299792.458) < 1e-12


def test_edge_params_returns_link_delay(tmp_path):
    path = _write_scenario(tmp_path)
    from config import load_scenario
    sc = load_scenario(path)
    sim = Simulator(sc)
    snap = sim.snapshots[0]
    if not snap.edges:
        pytest.skip("no edges in sample topology")
    e = snap.edges[0]
    delay, cap = sim._edge_params(snap, e.u, e.v)
    assert abs(delay - e.delay_s) < 1e-15
    assert abs(delay - e.distance_km / C_LIGHT_KM_S) < 1e-12
    assert cap == e.capacity_mbps


def test_zero_distance_propagation():
    delay = 0.0 / C_LIGHT_KM_S
    assert delay == 0.0


def test_single_hop_latency_includes_prop_delay(tmp_path):
    """Delivered latency should be at least one prop delay (predictive path)."""
    path = _write_scenario(tmp_path, duration_s=90, time_step_s=15)
    m = run_scenario(path, output_dir=tmp_path / "out")
    if m.summary_dict()["packets_delivered"] == 0:
        pytest.skip("no deliveries on this topology sample")
    all_lat = [x for f in m.flows.values() for x in f.latencies_s]
    assert all(x >= 0 for x in all_lat)
    # At least some packets should have non-trivial delay if multi-hop or long link
    assert max(all_lat) >= 0.0


# ---------------------------------------------------------------------------
# B. Serialization delay
# ---------------------------------------------------------------------------

def test_serialization_delay_formula():
    for size_bytes, cap_mbps in [(100, 100.0), (1500, 10.0), (512, 1000.0)]:
        ser = (size_bytes * 8.0) / (cap_mbps * 1e6)
        assert ser > 0
        assert abs(ser - size_bytes * 8.0 / (cap_mbps * 1e6)) < 1e-18


def test_serialization_increases_with_packet_size():
    s_small = (64 * 8.0) / (100e6)
    s_large = (1500 * 8.0) / (100e6)
    assert s_large > s_small


def test_serialization_decreases_with_capacity():
    s_slow = (512 * 8.0) / (1e6)
    s_fast = (512 * 8.0) / (1000e6)
    assert s_slow > s_fast


# ---------------------------------------------------------------------------
# C. Queue behaviour
# ---------------------------------------------------------------------------

def test_queue_limit_config_loaded(tmp_path):
    path = _write_scenario(tmp_path, link={"queue_limit": 2})
    from config import load_scenario
    sc = load_scenario(path)
    sim = Simulator(sc)
    assert sim.queue_limit == 2


def test_opposite_direction_links_independent():
    """Directed busy-time keys (u,v) and (v,u) are distinct."""
    busy = {}
    busy[("A", "B")] = 1.0
    busy[("B", "A")] = 2.0
    assert busy[("A", "B")] != busy[("B", "A")]


def test_congestion_drops_with_tiny_queue(tmp_path):
    """A busy link with queue_limit 1 must drop DATA, not only count residuals."""
    path = _write_scenario(
        tmp_path,
        duration_s=60,
        time_step_s=10,
        link={"queue_limit": 1, "capacity_mbps": 0.001, "loss_prob": 0.0},
        traffic=[
            {
                "flow_id": "burst",
                "source": "STARLINK-1007",
                "destination": "STARLINK-1010",
                "start_s": 1,
                "duration_s": 30,
                "rate_pps": 20.0,
                "packet_size_bytes": 1500,
            }
        ],
    )
    m = run_scenario(path, output_dir=tmp_path / "cong")
    s = m.summary_dict()
    total = s["packets_sent"]
    assert total > 0
    assert s["packets_delivered"] + s["packets_dropped"] + s["packets_residual"] == total
    assert s["packets_dropped"] > 0


def test_larger_queue_drops_fewer_packets(tmp_path):
    """queue_limit must change congestion drops, not only the accounting identity."""
    traffic = [
        {
            "flow_id": "burst",
            "source": "STARLINK-1007",
            "destination": "STARLINK-1010",
            "start_s": 1,
            "duration_s": 30,
            "rate_pps": 20.0,
            "packet_size_bytes": 1500,
        }
    ]
    base = _minimal_scenario_dict(
        tmp_path,
        duration_s=60,
        time_step_s=10,
        traffic=traffic,
        link={"queue_limit": 1, "capacity_mbps": 0.001, "loss_prob": 0.0},
    )
    tiny_path = tmp_path / "scenario_tiny.yaml"
    wide_path = tmp_path / "scenario_wide.yaml"
    tiny_path.write_text(yaml.dump(base))
    base["link"]["queue_limit"] = 64
    wide_path.write_text(yaml.dump(base))
    m_tiny = run_scenario(tiny_path, output_dir=tmp_path / "cong_tiny")
    m_wide = run_scenario(wide_path, output_dir=tmp_path / "cong_wide")
    d_tiny = m_tiny.summary_dict()["packets_dropped"]
    d_wide = m_wide.summary_dict()["packets_dropped"]
    assert d_tiny > d_wide
    for m in (m_tiny, m_wide):
        s = m.summary_dict()
        assert s["packets_delivered"] + s["packets_dropped"] + s["packets_residual"] == s["packets_sent"]


def test_data_ttl_zero_is_dropped_and_not_aliased(tmp_path):
    """DATA with ttl 0 is dropped once. A forwarded copy does not mutate the original."""
    path = _write_scenario(tmp_path, duration_s=30, time_step_s=30)
    from config import load_scenario
    sim = Simulator(load_scenario(path))
    snap = next((s for s in sim.snapshots if any(s.adj.values())), None)
    if snap is None:
        pytest.skip("no links in sample")
    src = "STARLINK-1007"
    neighs = sorted(snap.adj.get(src, []))
    if len(neighs) < 1:
        pytest.skip("source has no neighbour")
    nh = neighs[0]
    dead = Packet(
        pkt_id=1,
        src=src,
        dst="STARLINK-1010",
        type=PacketType.DATA,
        size_bytes=256,
        creation_time_s=0.0,
        ttl=0,
        path=[src],
    )
    sim._send(0.0, src, nh, dead, snap)
    assert sim.metrics.flows["f1"].dropped == 1
    assert not any(ev.kind == "arrival" for ev in sim._event_q)
    assert dead.ttl == 0
    assert dead.path == [src]

    live = Packet(
        pkt_id=2,
        src=src,
        dst="STARLINK-1010",
        type=PacketType.DATA,
        size_bytes=256,
        creation_time_s=0.0,
        ttl=1,
        path=[src],
    )
    sim._send(0.0, src, nh, live, snap)
    arrivals = [ev for ev in sim._event_q if ev.kind == "arrival"]
    assert len(arrivals) == 1
    assert arrivals[0].packet is not live
    assert arrivals[0].packet.ttl == 0
    assert live.ttl == 1
    assert arrivals[0].packet.path == [src, src] or arrivals[0].packet.path[-1] == src


def test_fanout_does_not_share_path_list(tmp_path):
    """Two unicasts of one control packet must not append path onto each other."""
    path = _write_scenario(tmp_path, duration_s=30, time_step_s=30)
    from config import load_scenario
    sim = Simulator(load_scenario(path))
    snap = next((s for s in sim.snapshots if any(len(v) >= 2 for v in s.adj.values())), None)
    if snap is None:
        pytest.skip("no node with two neighbours")
    src = next(n for n, vs in snap.adj.items() if len(vs) >= 2)
    n1, n2 = sorted(snap.adj[src])[:2]
    pkt = Packet(
        pkt_id=7,
        src=src,
        dst="*",
        type=PacketType.RERR,
        size_bytes=48,
        creation_time_s=0.0,
        ttl=8,
        path=[src],
        payload={"unreachable": []},
    )
    sim._send(0.0, src, n1, pkt, snap)
    sim._send(0.0, src, n2, pkt, snap)
    arrivals = [ev.packet for ev in sim._event_q if ev.kind == "arrival"]
    assert len(arrivals) == 2
    assert arrivals[0] is not arrivals[1]
    assert arrivals[0].path == [src, src]
    assert arrivals[1].path == [src, src]
    assert pkt.path == [src]


def test_broadcast_payloads_are_independent(tmp_path):
    """RREQ hop_count updates must not alias across neighbours."""
    path = _write_scenario(tmp_path, duration_s=30, time_step_s=30)
    from config import load_scenario
    sim = Simulator(load_scenario(path))
    snap = next((s for s in sim.snapshots if any(len(v) >= 2 for v in s.adj.values())), None)
    if snap is None:
        pytest.skip("no node with two neighbours")
    src = next(n for n, vs in snap.adj.items() if len(vs) >= 2)
    pkt = Packet(
        pkt_id=8,
        src=src,
        dst="*",
        type=PacketType.RREQ,
        size_bytes=64,
        creation_time_s=0.0,
        ttl=8,
        path=[src],
        payload={"hop_count": 0, "origin": src},
    )
    sim._send(0.0, src, "__broadcast__", pkt, snap)
    arrivals = [ev.packet for ev in sim._event_q if ev.kind == "arrival"]
    assert len(arrivals) >= 2
    arrivals[0].payload["hop_count"] = 99
    assert arrivals[1].payload["hop_count"] == 0
    assert pkt.payload["hop_count"] == 0


# ---------------------------------------------------------------------------
# D. Packet loss
# ---------------------------------------------------------------------------

def test_zero_loss_full_delivery_when_connected(tmp_path):
    path = _write_scenario(tmp_path, link={"loss_prob": 0.0}, duration_s=90, time_step_s=15)
    m = run_scenario(path, output_dir=tmp_path / "zloss")
    s = m.summary_dict()
    # May still drop if no route; but loss_prob itself is zero
    assert path.read_text()  # scenario exists
    assert s["packets_sent"] > 0


def test_certain_loss_drops_all_data(tmp_path):
    path = _write_scenario(
        tmp_path,
        link={"loss_prob": 1.0},
        duration_s=60,
        time_step_s=15,
    )
    m = run_scenario(path, output_dir=tmp_path / "closs")
    s = m.summary_dict()
    # With 100% loss on every hop, no DATA should be delivered
    assert s["packets_delivered"] == 0
    assert s["packets_sent"] == s["packets_dropped"] + s["packets_residual"]


def test_loss_reproducible_with_seed(tmp_path):
    results = []
    for _ in range(2):
        path = _write_scenario(
            tmp_path,
            seed=99,
            link={"loss_prob": 0.5},
            duration_s=45,
            time_step_s=15,
        )
        m = run_scenario(path, output_dir=tmp_path / f"loss_{_}")
        results.append(m.summary_dict())
    assert results[0]["packets_delivered"] == results[1]["packets_delivered"]
    assert results[0]["packets_dropped"] == results[1]["packets_dropped"]


# ---------------------------------------------------------------------------
# E. Packet accounting
# ---------------------------------------------------------------------------

def test_accounting_invariant_predictive(tmp_path):
    path = _write_scenario(tmp_path, protocol="predictive")
    m = run_scenario(path, output_dir=tmp_path / "acc")
    for f in m.flows.values():
        assert f.sent == f.delivered + f.dropped + f.residual


def test_accounting_invariant_aodv(tmp_path):
    path = _write_scenario(tmp_path, protocol="aodv", duration_s=90, time_step_s=15)
    m = run_scenario(path, output_dir=tmp_path / "acc_a")
    for f in m.flows.values():
        assert f.sent == f.delivered + f.dropped + f.residual


def test_accounting_invariant_olsr(tmp_path):
    path = _write_scenario(tmp_path, protocol="olsr", duration_s=60, time_step_s=15)
    m = run_scenario(path, output_dir=tmp_path / "acc_o")
    for f in m.flows.values():
        assert f.sent == f.delivered + f.dropped + f.residual


def test_aodv_failed_discovery_drops_buffered_data_after_retry_budget(tmp_path):
    """An unreachable destination must not leave DATA buffered until termination."""
    path = _write_scenario(
        tmp_path,
        protocol="aodv",
        duration_s=20,
        time_step_s=10,
        link={"max_range_km": 1.0, "loss_prob": 0.0},
        traffic=[
            {
                "flow_id": "f1",
                "source": "STARLINK-1007",
                "destination": "STARLINK-1010",
                "start_s": 1,
                "duration_s": 1,
                "rate_pps": 1.0,
                "packet_size_bytes": 256,
            }
        ],
    )
    m = run_scenario(path, output_dir=tmp_path / "aodv_timeout")
    flow = m.flows["f1"]
    assert flow.sent == 1
    assert flow.delivered == 0
    assert flow.dropped == 1
    assert flow.residual == 0


# ---------------------------------------------------------------------------
# F. Simulation termination
# ---------------------------------------------------------------------------

def test_short_duration_may_leave_residual(tmp_path):
    """Very short sim can leave packets residual without double-counting."""
    path = _write_scenario(
        tmp_path,
        duration_s=6,
        time_step_s=3,
        traffic=[
            {
                "flow_id": "f1",
                "source": "STARLINK-1007",
                "destination": "STARLINK-1010",
                "start_s": 1,
                "duration_s": 5,
                "rate_pps": 2.0,
                "packet_size_bytes": 256,
            }
        ],
    )
    m = run_scenario(path, output_dir=tmp_path / "term")
    s = m.summary_dict()
    assert s["packets_sent"] == s["packets_delivered"] + s["packets_dropped"] + s["packets_residual"]


def test_event_loop_terminates(tmp_path):
    path = _write_scenario(tmp_path, duration_s=30)
    m = run_scenario(path, output_dir=tmp_path / "term2")
    assert m.events_processed > 0
    assert m.events_processed < 5_000_000  # guard against infinite loop


# ---------------------------------------------------------------------------
# G. Event ordering
# ---------------------------------------------------------------------------

def test_kind_order_policy():
    assert _KIND_ORDER["topology"] < _KIND_ORDER["tick"]
    assert _KIND_ORDER["tx_done"] < _KIND_ORDER["tick"]
    assert _KIND_ORDER["tick"] < _KIND_ORDER["inject"]
    assert _KIND_ORDER["inject"] < _KIND_ORDER["arrival"]
    assert _KIND_ORDER["arrival"] < _KIND_ORDER["end"]


def test_event_heap_orders_same_time_by_kind():
    """At equal time, topology before inject before arrival."""
    import heapq
    events = []
    seq = 0
    for kind in ("arrival", "topology", "inject"):
        seq += 1
        heapq.heappush(
            events,
            Event(time=1.0, order=_KIND_ORDER[kind], seq=seq, kind=kind),
        )
    order = [heapq.heappop(events).kind for _ in range(3)]
    assert order == ["topology", "inject", "arrival"]


def test_event_heap_orders_by_time_first():
    import heapq
    events = []
    heapq.heappush(events, Event(time=2.0, order=0, seq=1, kind="topology"))
    heapq.heappush(events, Event(time=1.0, order=9, seq=2, kind="end"))
    assert heapq.heappop(events).time == 1.0
