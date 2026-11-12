"""
olsr.py – Simplified OLSR-style proactive / link-state routing.

Core ideas kept:
  - Periodic HELLO for neighbor sensing
  - MPR selection (greedy 2-hop cover) so only MPRs retransmit TC
  - Topology Control (TC) with (origin, seq) duplicate suppression
  - Dijkstra shortest-path computation from the local topology view

Not a full RFC 3626 implementation.
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
        self.neighbor_sets: Dict[str, Dict[str, Set[str]]] = {n: {} for n in nodes}
        self.topology: Dict[str, Dict[str, Set[str]]] = {n: {} for n in nodes}
        self.routing_table: Dict[str, Dict[str, str]] = {n: {} for n in nodes}
        self.mpr: Dict[str, Set[str]] = {n: set() for n in nodes}
        self.mpr_selectors: Dict[str, Set[str]] = {n: set() for n in nodes}
        self.last_hello: Dict[str, float] = {n: -1e9 for n in nodes}
        self.last_tc: Dict[str, float] = {n: -1e9 for n in nodes}
        self.seen_tc: Dict[str, Set[Tuple[str, int]]] = {n: set() for n in nodes}
        self._tc_seq: Dict[str, int] = {n: 0 for n in nodes}
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
            self.neighbor_sets[u].pop(v, None)
            self.neighbor_sets[v].pop(u, None)
        self._select_mpr(u)
        self._select_mpr(v)
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
            self.neighbor_sets[node][origin] = reported
            self.neighbors[node].add(origin)
            self.topology[node][origin] = reported
            if packet.payload.get("mpr") and node in packet.payload["mpr"]:
                self.mpr_selectors[node].add(origin)
            else:
                self.mpr_selectors[node].discard(origin)
            self._select_mpr(node)
            self._recompute(node)
            return None

        if packet.type == PacketType.TC:
            self._control_recv += 1
            origin = packet.payload.get("origin", packet.src)
            seq = packet.payload.get("seq", 0)
            key = (origin, seq)
            if key in self.seen_tc[node]:
                return None
            self.seen_tc[node].add(key)
            reported = set(packet.payload.get("neighbors", []))
            self.topology[node][origin] = reported
            self._recompute(node)
            prev = packet.path[-1] if packet.path else packet.src
            if packet.ttl > 1 and prev in self.mpr_selectors[node]:
                packet.ttl -= 1
                self._control_sent += max(0, len(self.neighbors[node]) - 1)
                return "__broadcast__"
            return None

        return None

    def tick(self, time_s: float, node: str) -> List[Packet]:
        out: List[Packet] = []
        if time_s - self.last_hello[node] >= self.hello_interval_s:
            self.last_hello[node] = time_s
            self._select_mpr(node)
            pkt = Packet(
                pkt_id=self._new_pkt_id(),
                src=node,
                dst="*",
                type=PacketType.HELLO,
                size_bytes=48,
                creation_time_s=time_s,
                ttl=1,
                path=[node],
                payload={
                    "neighbors": sorted(self.neighbors[node]),
                    "mpr": sorted(self.mpr[node]),
                },
            )
            self._control_sent += max(1, len(self.neighbors[node]))
            out.append(pkt)

        if time_s - self.last_tc[node] >= self.tc_interval_s:
            self.last_tc[node] = time_s
            self._tc_seq[node] += 1
            seq = self._tc_seq[node]
            pkt = Packet(
                pkt_id=self._new_pkt_id(),
                src=node,
                dst="*",
                type=PacketType.TC,
                size_bytes=64,
                creation_time_s=time_s,
                ttl=8,
                path=[node],
                payload={
                    "origin": node,
                    "seq": seq,
                    "neighbors": list(self.neighbors[node]),
                },
            )
            self._control_sent += max(1, len(self.neighbors[node]))
            out.append(pkt)
        return out

    def _select_mpr(self, node: str) -> None:
        """Greedy MPR: cover all 2-hop neighbors with a small set of 1-hop neighbors."""
        one_hop = set(self.neighbors[node])
        two_hop: Set[str] = set()
        for n1 in one_hop:
            reported = self.neighbor_sets[node].get(n1, set())
            for n2 in reported:
                if n2 != node and n2 not in one_hop:
                    two_hop.add(n2)
        if not two_hop:
            self.mpr[node] = set()
            return

        cover: Dict[str, Set[str]] = {}
        for n1 in one_hop:
            reported = self.neighbor_sets[node].get(n1, set())
            cover[n1] = {n2 for n2 in reported if n2 in two_hop}

        selected: Set[str] = set()
        remaining = set(two_hop)
        while remaining:
            best = None
            best_cover: Set[str] = set()
            for n1 in sorted(cover):\n                c = cover[n1]
                if n1 in selected:
                    continue
                hit = c & remaining
                if len(hit) > len(best_cover):
                    best = n1
                    best_cover = hit
            if best is None or not best_cover:
                break
            selected.add(best)
            remaining -= best_cover
        self.mpr[node] = selected

    def _recompute(self, node: str) -> None:
        graph: Dict[str, Set[str]] = defaultdict(set)
        for n in self.neighbors[node]:
            graph[node].add(n)
            graph[n].add(node)
        for origin, neighs in self.topology[node].items():
            for m in neighs:
                graph[origin].add(m)
                graph[m].add(origin)

        dist = {node: 0}
        prev: Dict[str, str] = {}
        pq = [(0, node)]
        visited: Set[str] = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            for v in sorted(graph.get(u, [])):
                nd = d + 1
                if v not in dist or nd < dist[v]:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        table: Dict[str, str] = {}
        for dest in dist:
            if dest == node:
                continue
            cur = dest
            while prev.get(cur) != node and cur in prev:
                cur = prev[cur]
            if prev.get(cur) == node:
                table[dest] = cur
        self.routing_table[node] = table

    def state_size(self, node: str) -> int:
        topo_bytes = sum(len(s) * 8 for s in self.topology[node].values())
        mpr_bytes = len(self.mpr[node]) * 8 + len(self.mpr_selectors[node]) * 8
        return (
            len(self.neighbors[node]) * 16
            + topo_bytes
            + len(self.routing_table[node]) * 24
            + mpr_bytes
            + 128
        )
