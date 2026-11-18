"""
Phase 2 routing tests: intermediate RREP, RERR, OLSR expiry, practical predictive lifetime.
"""

from __future__ import annotations

from links import TopologySnapshot, LinkEdge
from routing.aodv import AODVProtocol, RouteEntry
from routing.olsr import OLSRProtocol
from routing.practical_predictive import PracticalPredictiveProtocol
from routing.base import Packet, PacketType


NODES = ["A", "B", "C"]
EDGES = [("A", "B"), ("B", "C")]


def _snap(time_s: float = 0.0, dist: float = 1000.0) -> TopologySnapshot:
    edges = [
        LinkEdge(u=u, v=v, delay_s=0.001, capacity_mbps=100.0, distance_km=dist)
        for u, v in EDGES
    ]
    adj = {n: set() for n in NODES}
    for u, v in EDGES:
        adj[u].add(v)
        adj[v].add(u)
    return TopologySnapshot(time_s=time_s, edges=edges, adj=adj)


def _install(proto, edges=EDGES, t=0.0):
    for u, v in edges:
        proto.on_link_change(t, u, v, up=True)


# ---------------------------------------------------------------------------
# AODV intermediate RREP
# ---------------------------------------------------------------------------

def test_aodv_intermediate_rrep():
    """Node B with a fresh route to C should answer RREQ for C without flooding further."""
    proto = AODVProtocol(NODES, allow_intermediate_rrep=True)
    _install(proto)

    # B already has a route to C (as if previously discovered)
    proto.routing_table["B"]["C"] = RouteEntry(
        dest="C", next_hop="C", seq_no=5, hop_count=1, expiry_s=100.0
    )
    # Reverse path A known at B
    proto.routing_table["B"]["A"] = RouteEntry(
        dest="A", next_hop="A", seq_no=1, hop_count=1, expiry_s=100.0
    )

    rreq = Packet(
        pkt_id=1,
        src="A",
        dst="C",
        type=PacketType.RREQ,
        size_bytes=64,
        creation_time_s=0.0,
        ttl=10,
        path=["A"],
        payload={
            "origin": "A",
            "orig_seq": 1,
            "rreq_id": 1,
            "dest": "C",
            "dest_seq": 0,
            "hop_count": 0,
        },
    )
    result = proto.on_packet(0.0, "B", rreq)
    # Intermediate RREP → do not forward RREQ
    assert result is None
    out = proto.pop_outbound()
    assert any(o.packet.type == PacketType.RREP for o in out)
    rrep = next(o for o in out if o.packet.type == PacketType.RREP)
    assert rrep.packet.payload["dest"] == "C"
    assert rrep.next_hop == "A"


def test_aodv_rrep_forwarded_toward_origin():
    """An intermediate hop must return the next hop so the engine forwards the RREP."""
    proto = AODVProtocol(NODES)
    _install(proto)
    proto.routing_table["B"]["A"] = RouteEntry(
        dest="A", next_hop="A", seq_no=1, hop_count=1, expiry_s=100.0
    )
    proto.routing_table["B"]["C"] = RouteEntry(
        dest="C", next_hop="C", seq_no=5, hop_count=1, expiry_s=100.0
    )
    rrep = Packet(
        pkt_id=2,
        src="C",
        dst="A",
        type=PacketType.RREP,
        size_bytes=64,
        creation_time_s=1.0,
        ttl=10,
        path=["C"],
        payload={
            "dest": "C",
            "dest_seq": 5,
            "origin": "A",
            "hop_count": 0,
            "lifetime": 30.0,
        },
    )
    nh = proto.on_packet(1.0, "B", rrep)
    assert nh == "A"
    assert proto.routing_table["B"]["C"].next_hop == "C"


def test_aodv_newer_origin_seq_refreshes_reverse_route():
    """A longer path with a newer origin sequence must replace a short stale reverse route."""
    proto = AODVProtocol(NODES)
    _install(proto)
    proto.routing_table["B"]["A"] = RouteEntry(
        dest="A", next_hop="Z", seq_no=1, hop_count=1, expiry_s=10.0
    )
    rreq = Packet(
        pkt_id=3,
        src="A",
        dst="C",
        type=PacketType.RREQ,
        size_bytes=64,
        creation_time_s=9.0,
        ttl=10,
        path=["D"],
        payload={
            "origin": "A",
            "orig_seq": 2,
            "rreq_id": 2,
            "dest": "C",
            "dest_seq": 0,
            "hop_count": 4,
        },
    )
    proto.on_packet(9.0, "B", rreq)
    rev = proto.routing_table["B"]["A"]
    assert rev.next_hop == "D"
    assert rev.seq_no == 2
    assert rev.hop_count == 5
    assert proto.next_hop("B", "A", 10.5) == "D"


def test_aodv_rerr_on_link_break():
    """Link down should invalidate routes and emit RERR."""
    proto = AODVProtocol(NODES)
    _install(proto)
    proto.routing_table["A"]["C"] = RouteEntry(
        dest="C", next_hop="B", seq_no=2, hop_count=2, expiry_s=100.0,
        precursors={"X"},
    )
    proto.on_link_change(1.0, "A", "B", up=False)
    assert proto.next_hop("A", "C", 1.0) is None
    out = proto.pop_outbound()
    assert any(o.packet.type == PacketType.RERR for o in out)
    rerr = next(o for o in out if o.packet.type == PacketType.RERR)
    dests = [u["dest"] for u in rerr.packet.payload["unreachable"]]
    assert "C" in dests


def test_aodv_rerr_propagation():
    """Receiving RERR invalidates matching local routes."""
    proto = AODVProtocol(NODES)
    _install(proto)
    proto.routing_table["A"]["C"] = RouteEntry(
        dest="C", next_hop="B", seq_no=2, hop_count=2, expiry_s=100.0,
        precursors=set(),
    )
    rerr = Packet(
        pkt_id=9,
        src="B",
        dst="*",
        type=PacketType.RERR,
        size_bytes=48,
        creation_time_s=1.0,
        ttl=5,
        path=["B"],
        payload={"unreachable": [{"dest": "C", "seq": 3}]},
    )
    proto.on_packet(1.0, "A", rerr)
    assert proto.next_hop("A", "C", 1.0) is None


def test_aodv_discovery_retries_are_bounded_and_fail_buffered_data():
    proto = AODVProtocol(NODES, rreq_retries=1, rreq_retry_timeout_s=2.0)
    data = Packet(
        pkt_id=21,
        src="A",
        dst="C",
        type=PacketType.DATA,
        size_bytes=128,
        creation_time_s=0.0,
        path=["A"],
    )
    assert proto.on_packet(0.0, "A", data) is None
    assert proto.discovery_retries["A"]["C"] == 0
    proto.pop_outbound()  # Initial RREQ

    assert proto.retry_discovery(2.0, "A", "C") == []
    assert proto.discovery_retries["A"]["C"] == 1
    assert len(proto.pop_outbound()) == 1  # One bounded retry

    failed = proto.retry_discovery(4.0, "A", "C")
    assert failed == [data]
    assert "C" not in proto.pending["A"]
    assert "C" not in proto.buffer["A"]
    assert any(e.event_type == "route_fail" for e in proto.route_events)


# ---------------------------------------------------------------------------
# OLSR topology expiry
# ---------------------------------------------------------------------------

def test_olsr_topology_expiry():
    proto = OLSRProtocol(NODES, hello_interval_s=1.0, tc_interval_s=1.0, hold_time_s=5.0)
    _install(proto)
    # Inject a TC at t=0
    tc = Packet(
        pkt_id=1,
        src="C",
        dst="*",
        type=PacketType.TC,
        size_bytes=64,
        creation_time_s=0.0,
        ttl=8,
        path=["C"],
        payload={"origin": "C", "seq": 1, "neighbors": ["B"]},
    )
    proto.on_packet(0.0, "A", tc)
    assert "C" in proto.topology["A"]
    # After hold_time, expire
    proto.tick(6.0, "A")
    assert "C" not in proto.topology["A"]


# ---------------------------------------------------------------------------
# Practical predictive lifetime weighting
# ---------------------------------------------------------------------------

def test_practical_predictive_lifetime_from_range_rate():
    proto = PracticalPredictiveProtocol(NODES, horizon_s=30.0, max_range_km=5000.0)
    # First snapshot at close range
    snap0 = _snap(0.0, dist=1000.0)
    proto.update_from_snapshot(snap0, 0.0)
    # Second snapshot: range opening fast toward max
    snap1 = _snap(10.0, dist=4000.0)
    proto.update_from_snapshot(snap1, 10.0)
    # Opening 300 km/s → residual ~ (5000-4000)/300 ≈ 3.3 s < horizon
    life = proto._edge_lifetime.get(("A", "B"), 999)
    assert life < 30.0, f"expected short lifetime, got {life}"
    # Weight should be elevated
    w = proto._edge_weight.get(("A", "B"), 1.0)
    assert w > 1.0


def test_practical_predictive_routes_on_static():
    proto = PracticalPredictiveProtocol(NODES, horizon_s=60.0)
    snap = _snap(0.0)
    proto.update_from_snapshot(snap, 0.0)
    # Force recompute
    proto._last_recompute = -1e9
    proto.update_from_snapshot(snap, 10.0)
    assert proto.next_hop("A", "B", 10.0) == "B"
    assert proto.next_hop("A", "C", 10.0) == "B"
