"""
olsr.py – Simplified OLSR-style proactive / link-state routing.

Core ideas kept:
  - Periodic HELLO for neighbor sensing
  - Topology Control (TC) messages for MPR flooding (simplified: full flood)
  - Dijkstra shortest-path computation from the local topology view

Not a full RFC 3626 implementation; MPR selection is omitted for clarity.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

import heapq

from .base import Packet, PacketType, RoutingProtocol


class OLSRProtocol(RoutingProtocol):
    def __init__(
        self,
        nodes: List[str],
        hello_interval_s: float = 2.0,
        tc_interval_s: float = 5.0,
        hold_time_s: float = 15.0,
    ):
        super().__init__(nodes)
        self.hello_interval_s = hello_interval_s
        self.tc_interval_s = tc_interval_s
        self.hold_time_s = hold_time_s

        self.neighbors: Dict[str, Set[str]] = {n: set() for n in nodes}
        # topology: node -> set of neighbors reported by that node
        self.topology: Dict[str, Dict[str, Set[str]]] = {n: {} for n in nodes}
        self.routing_table: Dict[str, Dict[str, str]] = {n: {} for n in nodes}  # dest -> next_hop
        self.last_hello: Dict[str, float] = {n: -1e9 for n in nodes}
        self.last_tc: Dict[str, float] = {n: -1e9 for n in nodes}
        self._pkt_counter = 0

    def _new_pkt_id(self) -> int:
        self._pkt_counter += 1
        return self._pkt_counter

    def on_link_change(self, time_s: float, u: str, v: str, up: bool) -> None:
        if up:
            self.neighbors[u].add(v)
            self.neighbors[v].add(u)
        else:
            self.neighbors[u].discard(v)
            self.neighbors[v].discard(u)
        # Recompute routes for both endpoints
        self._recompute(u)
        self._recompute(v)

    def next_hop(self, node: str, destination: str, time_s: float) -> Optional[str]:
        if node == destination:
            return None
        return self.routing_table[node].get(destination)

    def on_packet(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        if packet.type == PacketType.DATA:
            if node == packet.dst:
                return None
            return self.next_hop(node, packet.dst, time_s)

        if packet.type == PacketType.HELLO:
            self._control_recv += 1
            origin = packet.src
            reported = set(packet.payload.get("neighbors", []))
            self.topology[node][origin] = reported
            self.neighbors[node].add(origin)
            self._recompute(node)
            return None

        if packet.type == PacketType.TC:
            self._control_recv += 1
            origin = packet.src
            reported = set(packet.payload.get("neighbors", []))
            self.topology[node][origin] = reported
            self._recompute(node)
            # Flood further (simplified)
            if packet.ttl > 1:
                packet.ttl -= 1
                self._control_sent += max(0, len(self.neighbors[node]) - 1)
                return "__broadcast__"
            return None

        return None

    def tick(self, time_s: float, node: str) -> List[Packet]:
        """Called periodically by the engine to emit HELLO / TC."""
        out: List[Packet] = []
        if time_s - self.last_hello[node] >= self.hello_interval_s:
            self.last_hello[node] = time_s
            pkt = Packet(
                pkt_id=self._new_pkt_id(),
                src=node,
                dst="*",
                type=PacketType.HELLO,
                size_bytes=48,
                creation_time_s=time_s,
                ttl=1,
                payload={"neighbors": list(self.neighbors[node])},
            )
            self._control_sent += len(self.neighbors[node])
            out.append(pkt)

        if time_s - self.last_tc[node] >= self.tc_interval_s:
            self.last_tc[node] = time_s
            pkt = Packet(
                pkt_id=self._new_pkt_id(),
                src=node,
                dst="*",
                type=PacketType.TC,
                size_bytes=64,
                creation_time_s=time_s,
                ttl=15,
                payload={"neighbors": list(self.neighbors[node])},
            )
            self._control_sent += len(self.neighbors[node])
            out.append(pkt)
        return out

    def _recompute(self, node: str) -> None:
        """Dijkstra on the local topology view."""
        # Build undirected graph from neighbor + topology info
        graph: Dict[str, Set[str]] = defaultdict(set)
        for n in self.neighbors[node]:
            graph[node].add(n)
            graph[n].add(node)
        for origin, neighs in self.topology[node].items():
            for m in neighs:
                graph[origin].add(m)
                graph[m].add(origin)

        # Dijkstra
        dist = {node: 0}
        prev: Dict[str, str] = {}
        pq = [(0, node)]
        visited: Set[str] = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            for v in graph.get(u, []):
                nd = d + 1
                if v not in dist or nd < dist[v]:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        # Next hops
        table: Dict[str, str] = {}
        for dest in dist:
            if dest == node:
                continue
            # Walk back to find the first hop
            cur = dest
            while prev.get(cur) != node and cur in prev:
                cur = prev[cur]
            if prev.get(cur) == node:
                table[dest] = cur
        self.routing_table[node] = table

    def state_size(self, node: str) -> int:
        # neighbors + topology entries + routing table
        topo_bytes = sum(len(s) * 8 for s in self.topology[node].values())
        return len(self.neighbors[node]) * 16 + topo_bytes + len(self.routing_table[node]) * 24 + 128
