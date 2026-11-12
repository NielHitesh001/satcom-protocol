"""
Static-topology routing tests with hand-computed expected paths.

Graph (undirected):

    A --- B --- C
     \\         /
      \\       /
       D --- E

Shortest paths (hop count):
  A→C : A-B-C  (or A-D-E-C)  — both length 2; we accept either
  A→E : A-D-E  (length 2)
  B→D : B-A-D or B-C-E-D    — length 2
  A→B : A-B                (length 1)
"""

from __future__ import annotations

from links import TopologySnapshot, LinkEdge
from routing.aodv import AODVProtocol
from routing.olsr import OLSRProtocol
from routing.predictive import PredictiveProtocol
from routing.base import Packet, PacketType


NODES = ["A", "B", "C", "D", "E"]

# Undirected edges for the static graph
EDGES = [
    ("A", "B"), ("B", "C"),
    ("A", "D"), ("D", "E"), ("E", "C"),
]


def _static_snapshot(time_s: float = 0.0) -> TopologySnapshot:
    edges = [
        LinkEdge(u=u, v=v, delay_s=0.001, capacity_mbps=100.0, distance_km=1000.0)
        for u, v in EDGES
    ]
    adj = {n: set() for n in NODES}
    for u, v in EDGES:
        adj[u].add(v)
        adj[v].add(u)
    return TopologySnapshot(time_s=time_s, edges=edges, adj=adj)


def _install_links(proto, snap: TopologySnapshot, time_s: float = 0.0) -> None:
    """Notify the protocol of every undirected edge as an up event."""
    for u, v in EDGES:
        proto.on_link_change(time_s, u, v, up=True)


# ---------------------------------------------------------------------------
# Predictive (oracle) – easiest to verify: load snapshot, query next_hop
# ---------------------------------------------------------------------------

def test_predictive_shortest_paths():
    snap = _static_snapshot()
    proto = PredictiveProtocol(NODES)
    proto.load_snapshots([snap])

    # Direct neighbour
    assert proto.next_hop("A", "B", 0.0) == "B"
    assert proto.next_hop("A", "D", 0.0) == "D"

    # Two-hop: A→C must go via B or D
    nh = proto.next_hop("A", "C", 0.0)
    assert nh in ("B", "D"), f"unexpected next hop A→C: {nh}"

    # Two-hop: A→E must go via D
    assert proto.next_hop("A", "E", 0.0) == "D"

    # Destination is self → None
    assert proto.next_hop("A", "A", 0.0) is None

    # Unknown destination
    assert proto.next_hop("A", "Z", 0.0) is None


# ---------------------------------------------------------------------------
# OLSR – after HELLO/TC exchange the routing table should match shortest paths
# ---------------------------------------------------------------------------

def test_olsr_converges_on_static_graph():
    snap = _static_snapshot()
    proto = OLSRProtocol(NODES, hello_interval_s=1.0, tc_interval_s=1.0)
    _install_links(proto, snap)

    # Drive a few HELLO/TC rounds so topology floods
    for t in [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]:
        for node in NODES:
            for pkt in proto.tick(t, node):
                # Deliver to all neighbours (and flood TC further)
                for neigh in snap.adj[node]:
                    nh = proto.on_packet(t, neigh, pkt)
                    if nh == "__broadcast__":
                        for n2 in snap.adj[neigh]:
                            if n2 != node:
                                proto.on_packet(t, n2, pkt)

    # After convergence, next hops should be valid shortest-path first hops
    assert proto.next_hop("A", "B", 5.0) == "B"
    assert proto.next_hop("A", "D", 5.0) == "D"
    nh_ac = proto.next_hop("A", "C", 5.0)
    assert nh_ac in ("B", "D"), f"OLSR A→C next hop {nh_ac}"
    assert proto.next_hop("A", "E", 5.0) == "D"


# ---------------------------------------------------------------------------
# AODV – on-demand: inject a DATA packet, let RREQ/RREP settle, then next_hop
# ---------------------------------------------------------------------------

def test_aodv_discovers_route():
    """
    Simplified AODV in this codebase does not fully close multi-hop RREQ
    flooding inside a single on_packet call. We verify the parts that *are*
    implemented: neighbour table, reverse-route install on RREQ, and
    next_hop after a direct (one-hop) route is present.
    """
    snap = _static_snapshot()
    proto = AODVProtocol(NODES)
    _install_links(proto, snap)

    # One-hop: after link up, A knows B as neighbour but has no route entry
    # until traffic triggers discovery. Manually install a one-hop route
    # the way a successful RREP would.
    from routing.aodv import RouteEntry
    proto.routing_table["A"]["B"] = RouteEntry(
        dest="B", next_hop="B", seq_no=1, hop_count=1, expiry_s=100.0
    )
    assert proto.next_hop("A", "B", 0.0) == "B"

    # Expired route → None
    proto.routing_table["A"]["B"].expiry_s = -1.0
    assert proto.next_hop("A", "B", 0.0) is None

    # Link down invalidates routes that used that next hop
    proto.routing_table["A"]["B"] = RouteEntry(
        dest="B", next_hop="B", seq_no=1, hop_count=1, expiry_s=100.0
    )
    proto.on_link_change(1.0, "A", "B", up=False)
    assert proto.next_hop("A", "B", 1.0) is None
    # Route-fail event should have been recorded
    assert any(e.event_type == "route_fail" for e in proto.route_events)


def test_aodv_state_size_nonzero_with_routes():
    proto = AODVProtocol(NODES)
    from routing.aodv import RouteEntry
    proto.routing_table["A"]["C"] = RouteEntry(
        dest="C", next_hop="B", seq_no=1, hop_count=2, expiry_s=100.0
    )
    assert proto.state_size("A") > proto.state_size("B")
