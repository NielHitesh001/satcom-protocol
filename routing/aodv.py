"""
aodv.py – Simplified AODV-style on-demand routing.

Implements:
  - Route Request (RREQ) flooding with duplicate suppression
  - Destination and intermediate RREP
  - Sequence numbers & route expiry
  - RERR on link breakage (precursor notification)
  - DATA buffering during discovery

Not a full RFC 3561 implementation.
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
        rreq_retry_timeout_s: float = 5.0,
        net_diameter: int = 15,
        buffer_limit: int = 32,
        allow_intermediate_rrep: bool = True,
        size_model=None,
    ):
        super().__init__(nodes, size_model=size_model)
        self.route_timeout_s = route_timeout_s
        self.rreq_retries = max(0, rreq_retries)
        self.rreq_retry_timeout_s = max(0.001, rreq_retry_timeout_s)
        self.net_diameter = net_diameter
        self.buffer_limit = buffer_limit
        self.allow_intermediate_rrep = allow_intermediate_rrep

        self.routing_table: Dict[str, Dict[str, RouteEntry]] = {n: {} for n in nodes}
        self.seq_no: Dict[str, int] = {n: 0 for n in nodes}
        self.rreq_id: Dict[str, int] = {n: 0 for n in nodes}
        self.seen_rreq: Dict[str, Set[Tuple[str, int]]] = {n: set() for n in nodes}
        self.pending: Dict[str, Dict[str, float]] = {n: {} for n in nodes}
        # Number of retransmissions after the initial RREQ for each discovery.
        self.discovery_retries: Dict[str, Dict[str, int]] = {n: {} for n in nodes}
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
            self._invalidate_and_rerr(time_s, u, v)
            self._invalidate_and_rerr(time_s, v, u)

    def _invalidate_and_rerr(self, time_s: float, node: str, broken_next: str) -> None:
        """Remove routes via broken_next and emit RERR to precursors."""
        table = self.routing_table[node]
        unreachable: List[Tuple[str, int]] = []
        precursors: Set[str] = set()
        to_remove = []
        for d, e in table.items():
            if e.next_hop == broken_next:
                unreachable.append((d, e.seq_no))
                precursors |= e.precursors
                to_remove.append(d)
                self.record_event(time_s, "route_fail", node, d, f"link to {broken_next} down")
        for d in to_remove:
            del table[d]

        if not unreachable:
            return

        # Emit RERR to precursors (and broadcast if none tracked)
        rerr_size = self.size_model.rerr_size(len(unreachable))
        rerr = Packet(
            pkt_id=self._new_pkt_id(),
            src=node,
            dst="*",
            type=PacketType.RERR,
            size_bytes=rerr_size,
            creation_time_s=time_s,
            ttl=8,
            path=[node],
            payload={
                "unreachable": [{"dest": d, "seq": s} for d, s in unreachable],
            },
        )
        targets = precursors if precursors else set(self.neighbors[node])
        if not targets:
            self.account_control("RERR", 1, rerr_size)
            self.outbound.append(Outbound(node=node, packet=rerr, next_hop="__broadcast__"))
        else:
            for t in sorted(targets):
                self.account_control("RERR", 1, rerr_size)
                self.outbound.append(
                    Outbound(node=node, packet=rerr, next_hop=t)
                )

    def next_hop(self, node: str, destination: str, time_s: float) -> Optional[str]:
        if node == destination:
            return None
        entry = self.routing_table[node].get(destination)
        if entry is None:
            return None
        if entry.expiry_s < time_s:
            del self.routing_table[node][destination]
            return None
        return entry.next_hop

    def on_packet(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        if packet.type == PacketType.DATA:
            return self._handle_data(time_s, node, packet)
        if packet.type == PacketType.RREQ:
            return self._handle_rreq(time_s, node, packet)
        if packet.type == PacketType.RREP:
            return self._handle_rrep(time_s, node, packet)
        if packet.type == PacketType.RERR:
            return self._handle_rerr(time_s, node, packet)
        return None

    def _handle_data(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        if node == packet.dst:
            return None
        nh = self.next_hop(node, packet.dst, time_s)
        if nh is not None:
            # Track precursor: who sent us this packet may need RERR later
            prev = packet.path[-1] if packet.path else None
            entry = self.routing_table[node].get(packet.dst)
            if entry is not None and prev and prev != node:
                entry.precursors.add(prev)
            return nh
        buf = self.buffer[node][packet.dst]
        if len(buf) < self.buffer_limit:
            buf.append(packet)
        if packet.dst not in self.pending[node]:
            self.pending[node][packet.dst] = time_s
            self.discovery_retries[node][packet.dst] = 0
            self.record_event(time_s, "discovery_start", node, packet.dst)
            self._emit_rreq(time_s, node, packet.dst)
        return None

    def retry_discovery(self, time_s: float, node: str, dest: str) -> List[Packet]:
        """Retry an unresolved discovery or fail its buffered DATA deterministically.

        The initial RREQ is emitted by :meth:`_handle_data`.  ``rreq_retries``
        therefore counts additional RREQ transmissions, avoiding an unbounded
        pending buffer when the destination is unreachable or every RREP is
        lost.  The engine owns the returned DATA packets and records their
        terminal drops.
        """
        if dest not in self.pending[node]:
            return []
        retries = self.discovery_retries[node].get(dest, 0)
        if retries < self.rreq_retries:
            self.discovery_retries[node][dest] = retries + 1
            self._emit_rreq(time_s, node, dest)
            return []

        buffered = self.buffer[node].pop(dest, [])
        self.pending[node].pop(dest, None)
        self.discovery_retries[node].pop(dest, None)
        self.record_event(time_s, "route_fail", node, dest, "RREQ retry budget exhausted")
        return buffered

    def _emit_rreq(self, time_s: float, origin: str, dest: str) -> None:
        self.seq_no[origin] += 1
        self.rreq_id[origin] += 1
        dest_seq = 0
        existing = self.routing_table[origin].get(dest)
        if existing is not None:
            dest_seq = existing.seq_no
        rreq = Packet(
            pkt_id=self._new_pkt_id(),
            src=origin,
            dst=dest,
            type=PacketType.RREQ,
            size_bytes=self.size_model.aodv_rreq,
            creation_time_s=time_s,
            ttl=self.net_diameter,
            path=[origin],
            payload={
                "origin": origin,
                "orig_seq": self.seq_no[origin],
                "rreq_id": self.rreq_id[origin],
                "dest": dest,
                "dest_seq": dest_seq,
                "hop_count": 0,
            },
        )
        self.account_control("RREQ", max(1, len(self.neighbors[origin])), rreq.size_bytes)
        self.outbound.append(Outbound(node=origin, packet=rreq, next_hop="__broadcast__"))

    def _emit_rrep(
        self,
        time_s: float,
        node: str,
        origin: str,
        dest: str,
        dest_seq: int,
        hop_count: int,
        lifetime: float,
    ) -> None:
        rrep = Packet(
            pkt_id=self._new_pkt_id(),
            src=node,
            dst=origin,
            type=PacketType.RREP,
            size_bytes=self.size_model.aodv_rrep,
            creation_time_s=time_s,
            path=[node],
            payload={
                "dest": dest,
                "dest_seq": dest_seq,
                "origin": origin,
                "hop_count": hop_count,
                "lifetime": lifetime,
            },
        )
        nh = self.next_hop(node, origin, time_s)
        if nh:
            self.account_control("RREP", 1, rrep.size_bytes)
            self.outbound.append(Outbound(node=node, packet=rrep, next_hop=nh))

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

        # Reverse route toward origin. A newer origin sequence must replace a
        # shorter stale route; otherwise the RREP for this RREQ is stranded
        # when the old entry expires.
        existing = self.routing_table[node].get(origin)
        incoming_seq = p["orig_seq"]
        if (
            existing is None
            or existing.expiry_s < time_s
            or incoming_seq > existing.seq_no
            or (incoming_seq == existing.seq_no and hop < existing.hop_count)
        ):
            self.routing_table[node][origin] = RouteEntry(
                dest=origin,
                next_hop=prev_hop,
                seq_no=incoming_seq,
                hop_count=hop,
                expiry_s=time_s + self.route_timeout_s,
            )
        else:
            existing.precursors.add(prev_hop)

        dest = p["dest"]

        # Destination generates RREP
        if node == dest:
            self.seq_no[node] += 1
            self._emit_rrep(
                time_s, node, origin, dest,
                dest_seq=self.seq_no[node],
                hop_count=0,
                lifetime=self.route_timeout_s,
            )
            return None

        # Intermediate RREP if we have a fresh route to dest
        if self.allow_intermediate_rrep:
            route = self.routing_table[node].get(dest)
            if (
                route is not None
                and route.expiry_s >= time_s
                and route.seq_no >= p.get("dest_seq", 0)
                and route.hop_count >= 1
            ):
                # Gratuitous RREP toward dest is omitted; reply toward origin
                self._emit_rrep(
                    time_s, node, origin, dest,
                    dest_seq=route.seq_no,
                    hop_count=route.hop_count,
                    lifetime=route.expiry_s - time_s,
                )
                # Record precursor on the forward route
                route.precursors.add(prev_hop)
                return None

        if packet.ttl > 1:
            packet.ttl -= 1
            p["hop_count"] = hop
            self.account_control("RREQ", max(0, len(self.neighbors[node]) - 1), packet.size_bytes)
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
        else:
            existing.precursors.add(prev)

        if node == p["origin"]:
            self.pending[node].pop(dest, None)
            self.discovery_retries[node].pop(dest, None)
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
            self.account_control("RREP", 1, packet.size_bytes)
            return nh
        return None

    def _handle_rerr(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        self._control_recv += 1
        unreachable = packet.payload.get("unreachable", [])
        if not unreachable:
            return None

        table = self.routing_table[node]
        newly_unreachable: List[Tuple[str, int]] = []
        precursors: Set[str] = set()
        for item in unreachable:
            dest = item["dest"]
            seq = item.get("seq", 0)
            entry = table.get(dest)
            if entry is None:
                continue
            # Invalidate if this route used a next hop that led toward the broken path
            # Simplified: invalidate if seq is not older
            if entry.seq_no <= seq or entry.expiry_s < time_s:
                precursors |= entry.precursors
                newly_unreachable.append((dest, max(entry.seq_no, seq)))
                del table[dest]
                self.record_event(time_s, "route_fail", node, dest, "RERR")

        if not newly_unreachable or packet.ttl <= 1:
            return None

        # Propagate RERR upstream
        packet.ttl -= 1
        packet.payload = {
            "unreachable": [{"dest": d, "seq": s} for d, s in newly_unreachable],
        }
        targets = precursors if precursors else set(self.neighbors[node]) - {packet.path[-1] if packet.path else ""}
        if not targets:
            self.account_control("RERR", 1, packet.size_bytes)
            return "__broadcast__"
        # Unicast to each precursor via outbound
        for t in targets:
            self.account_control("RERR", 1, packet.size_bytes)
            self.outbound.append(
                Outbound(
                    node=node,
                    packet=Packet(
                        pkt_id=self._new_pkt_id(),
                        src=node,
                        dst="*",
                        type=PacketType.RERR,
                        size_bytes=packet.size_bytes,
                        creation_time_s=time_s,
                        ttl=packet.ttl,
                        path=list(packet.path) + [node],
                        payload=packet.payload,
                    ),
                    next_hop=t,
                )
            )
        return None

    def state_size(self, node: str) -> int:
        n_routes = len(self.routing_table[node])
        n_buf = sum(len(v) for v in self.buffer[node].values())
        return n_routes * 48 + n_buf * 64 + 64
