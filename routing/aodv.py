"""
aodv.py – Simplified AODV-style on-demand routing.

Implements the core ideas:
  - Route Request (RREQ) flooding
  - Route Reply (RREP) unicasting
  - Sequence numbers & route expiry
  - Basic RERR on link breakage
  - Data buffering during discovery

This is intentionally simplified for the simulator; it is not a full
RFC 3561 implementation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .base import Packet, PacketType, RoutingProtocol


@dataclass
class RouteEntry:
    dest: str
    next_hop: str
    seq_no: int
    hop_count: int
    expiry_s: float
    precursors: Set[str] = field(default_factory=set)


@dataclass
class Outbound:
    """A packet the engine should inject into the network."""
    node: str
    packet: Packet
    next_hop: Optional[str]


class AODVProtocol(RoutingProtocol):
    def __init__(
        self,
        nodes: List[str],
        route_timeout_s: float = 60.0,
        rreq_retries: int = 2,
        net_diameter: int = 15,
        buffer_limit: int = 32,
    ):
        super().__init__(nodes)
        self.route_timeout_s = route_timeout_s
        self.rreq_retries = rreq_retries
        self.net_diameter = net_diameter
        self.buffer_limit = buffer_limit

        self.routing_table: Dict[str, Dict[str, RouteEntry]] = {n: {} for n in nodes}
        self.seq_no: Dict[str, int] = {n: 0 for n in nodes}
        self.rreq_id: Dict[str, int] = {n: 0 for n in nodes}
        self.seen_rreq: Dict[str, Set[Tuple[str, int]]] = {n: set() for n in nodes}
        self.pending: Dict[str, Dict[str, float]] = {n: {} for n in nodes}
        self.neighbors: Dict[str, Set[str]] = {n: set() for n in nodes}
        self.buffer: Dict[str, Dict[str, List[Packet]]] = {
            n: defaultdict(list) for n in nodes
        }
        self.outbound: List[Outbound] = []
        self._pkt_counter = 0

    def _new_pkt_id(self) -> int:
        self._pkt_counter += 1
        return self._pkt_counter

    def pop_outbound(self) -> List[Outbound]:
        out = self.outbound
        self.outbound = []
        return out

    def on_link_change(self, time_s: float, u: str, v: str, up: bool) -> None:
        if up:
            self.neighbors[u].add(v)
            self.neighbors[v].add(u)
            self.routing_table[u][v] = RouteEntry(
                dest=v, next_hop=v, seq_no=0, hop_count=1,
                expiry_s=time_s + self.route_timeout_s,
            )
            self.routing_table[v][u] = RouteEntry(
                dest=u, next_hop=u, seq_no=0, hop_count=1,
                expiry_s=time_s + self.route_timeout_s,
            )
        else:
            self.neighbors[u].discard(v)
            self.neighbors[v].discard(u)
            self._invalidate_via(time_s, u, v)
            self._invalidate_via(time_s, v, u)

    def _invalidate_via(self, time_s: float, node: str, broken_next: str) -> None:
        table = self.routing_table[node]
        to_remove = [d for d, e in table.items() if e.next_hop == broken_next]
        for d in to_remove:
            del table[d]
            self.record_event(time_s, "route_fail", node, d, f"link to {broken_next} down")

    def next_hop(self, node: str, destination: str, time_s: float) -> Optional[str]:
        if node == destination:
            return None
        entry = self.routing_table[node].get(destination)
        if entry is None or entry.expiry_s < time_s:
            return None
        return entry.next_hop

    def on_packet(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        if packet.type == PacketType.DATA:
            return self._handle_data(time_s, node, packet)
        if packet.type == PacketType.RREQ:
            return self._handle_rreq(time_s, node, packet)
        if packet.type == PacketType.RREP:
            return self._handle_rrep(time_s, node, packet)
        return None

    def _handle_data(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        if node == packet.dst:
            return None
        nh = self.next_hop(node, packet.dst, time_s)
        if nh is not None:
            return nh
        buf = self.buffer[node][packet.dst]
        if len(buf) < self.buffer_limit:
            buf.append(packet)
        if packet.dst not in self.pending[node]:
            self.pending[node][packet.dst] = time_s
            self.record_event(time_s, "discovery_start", node, packet.dst)
            self._emit_rreq(time_s, node, packet.dst)
        return None

    def _emit_rreq(self, time_s: float, origin: str, dest: str) -> None:
        self.seq_no[origin] += 1
        self.rreq_id[origin] += 1
        rreq = Packet(
            pkt_id=self._new_pkt_id(),
            src=origin,
            dst=dest,
            type=PacketType.RREQ,
            size_bytes=64,
            creation_time_s=time_s,
            ttl=self.net_diameter,
            path=[origin],
            payload={
                "origin": origin,
                "orig_seq": self.seq_no[origin],
                "rreq_id": self.rreq_id[origin],
                "dest": dest,
                "dest_seq": 0,
                "hop_count": 0,
            },
        )
        self._control_sent += max(1, len(self.neighbors[origin]))
        self.outbound.append(Outbound(node=origin, packet=rreq, next_hop="__broadcast__"))

    def _handle_rreq(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        p = packet.payload
        origin = p["origin"]
        rreq_id = p["rreq_id"]
        key = (origin, rreq_id)
        if key in self.seen_rreq[node]:
            return None
        self.seen_rreq[node].add(key)
        self._control_recv += 1

        hop = p["hop_count"] + 1
        prev_hop = packet.path[-1] if packet.path else origin

        existing = self.routing_table[node].get(origin)
        if existing is None or hop < existing.hop_count or existing.expiry_s < time_s:
            self.routing_table[node][origin] = RouteEntry(
                dest=origin,
                next_hop=prev_hop,
                seq_no=p["orig_seq"],
                hop_count=hop,
                expiry_s=time_s + self.route_timeout_s,
            )

        if node == p["dest"]:
            self.seq_no[node] += 1
            rrep = Packet(
                pkt_id=self._new_pkt_id(),
                src=node,
                dst=origin,
                type=PacketType.RREP,
                size_bytes=64,
                creation_time_s=time_s,
                path=[node],
                payload={
                    "dest": node,
                    "dest_seq": self.seq_no[node],
                    "origin": origin,
                    "hop_count": 0,
                    "lifetime": self.route_timeout_s,
                },
            )
            nh = self.next_hop(node, origin, time_s)
            if nh:
                self._control_sent += 1
                self.outbound.append(Outbound(node=node, packet=rrep, next_hop=nh))
                self.record_event(time_s, "discovery_end", origin, node, "RREP generated")
            return None

        if packet.ttl > 1:
            packet.ttl -= 1
            p["hop_count"] = hop
            self._control_sent += max(0, len(self.neighbors[node]) - 1)
            return "__broadcast__"
        return None

    def _handle_rrep(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        p = packet.payload
        self._control_recv += 1
        dest = p["dest"]
        hop = p["hop_count"] + 1
        prev = packet.path[-1] if packet.path else dest

        existing = self.routing_table[node].get(dest)
        if existing is None or hop <= existing.hop_count or existing.expiry_s < time_s:
            self.routing_table[node][dest] = RouteEntry(
                dest=dest,
                next_hop=prev,
                seq_no=p["dest_seq"],
                hop_count=hop,
                expiry_s=time_s + p["lifetime"],
            )

        if node == p["origin"]:
            self.pending[node].pop(dest, None)
            self.record_event(time_s, "discovery_end", node, dest, "route installed")
            buffered = self.buffer[node].pop(dest, [])
            nh = self.next_hop(node, dest, time_s)
            if nh:
                for data_pkt in buffered:
                    self.outbound.append(Outbound(node=node, packet=data_pkt, next_hop=nh))
            return None

        nh = self.next_hop(node, p["origin"], time_s)
        if nh:
            p["hop_count"] = hop
            self._control_sent += 1
            return nh
        return None

    def state_size(self, node: str) -> int:
        n_routes = len(self.routing_table[node])
        n_buf = sum(len(v) for v in self.buffer[node].values())
        return n_routes * 40 + n_buf * 64 + 64
