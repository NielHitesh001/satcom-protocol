"""
olsr.py – Simplified OLSR-style proactive / link-state routing.

Core ideas:
  - Periodic HELLO for neighbor sensing
  - MPR selection (greedy 2-hop cover)
  - TC with (origin, seq) duplicate suppression and MPR flooding
  - Topology / neighbor entry expiry (hold_time)
  - Dijkstra on the local topology view

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
        size_model=None,
    ):
        super().__init__(nodes, size_model=size_model)
        self.hello_interval_s = hello_interval_s
        self.tc_interval_s = tc_interval_s
        self.hold_time_s = hold_time_s

        self.neighbors: Dict[str, Set[str]] = {n: set() for n in nodes}
        self.neighbor_sets: Dict[str, Dict[str, Set[str]]] = {n: {} for n in nodes}
        # topology[node][origin] = (neighbor_set, last_update_time)
        self.topology: Dict[str, Dict[str, Tuple[Set[str], float]]] = {n: {} for n in nodes}
        self.neighbor_time: Dict[str, Dict[str, float]] = {n: {} for n in nodes}
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
            self.neighbor_time[u][v] = time_s
            self.neighbor_time[v][u] = time_s
        else:
            self.neighbors[u].discard(v)
            self.neighbors[v].discard(u)
            self.neighbor_sets[u].pop(v, None)
            self.neighbor_sets[v].pop(u, None)
            self.neighbor_time[u].pop(v, None)
            self.neighbor_time[v].pop(u, None)
            self.mpr_selectors[u].discard(v)
            self.mpr_selectors[v].discard(u)
        self._select_mpr(u)
        self._select_mpr(v)
        self._recompute(u, time_s)
        self._recompute(v, time_s)

    def next_hop(self, node: str, destination: str, time_s: float) -> Optional[str]:
        if node == destination:
            return None
        return self.routing_table[node].get(destination)

    def _expire(self, node: str, time_s: float) -> None:
        """Drop topology and neighbour entries older than hold_time."""
        stale_topo = [
            origin
            for origin, (_neighs, t0) in self.topology[node].items()
            if time_s - t0 > self.hold_time_s
        ]
        for origin in stale_topo:
            del self.topology[node][origin]

        stale_n = [
            n
            for n, t0 in self.neighbor_time[node].items()
            if time_s - t0 > self.hold_time_s
        ]
        for n in stale_n:
            self.neighbors[node].discard(n)
            self.neighbor_sets[node].pop(n, None)
            self.neighbor_time[node].pop(n, None)
            self.mpr_selectors[node].discard(n)

        # Bound seen_tc size
        if len(self.seen_tc[node]) > 5000:
            self.seen_tc[node].clear()

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
            self.neighbor_time[node][origin] = time_s
            self.topology[node][origin] = (reported, time_s)
            if packet.payload.get("mpr") and node in packet.payload["mpr"]:
                self.mpr_selectors[node].add(origin)
            else:
                self.mpr_selectors[node].discard(origin)
            self._select_mpr(node)
            self._recompute(node, time_s)
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
            self.topology[node][origin] = (reported, time_s)
            self._recompute(node, time_s)
            prev = packet.path[-1] if packet.path else packet.src
            if packet.ttl > 1 and prev in self.mpr_selectors[node]:
                packet.ttl -= 1
                self.account_control("TC", max(0, len(self.neighbors[node]) - 1), packet.size_bytes)
                return "__broadcast__"
            return None

        return None

    def tick(self, time_s: float, node: str) -> List[Packet]:
        self._expire(node, time_s)
        out: List[Packet] = []
        if time_s - self.last_hello[node] >= self.hello_interval_s:
            self.last_hello[node] = time_s
            self._select_mpr(node)
            pkt = Packet(
                pkt_id=self._new_pkt_id(),
                src=node,
                dst="*",
                type=PacketType.HELLO,
                size_bytes=self.size_model.olsr_hello,
                creation_time_s=time_s,
                ttl=1,
                path=[node],
                payload={
                    "neighbors": list(self.neighbors[node]),
                    "mpr": list(self.mpr[node]),
                },
            )
            self.account_control("HELLO", max(1, len(self.neighbors[node])), pkt.size_bytes)
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
                size_bytes=self.size_model.olsr_tc,
                creation_time_s=time_s,
                ttl=8,
                path=[node],
                payload={
                    "origin": node,
                    "seq": seq,
                    "neighbors": list(self.neighbors[node]),
                },
            )
            self.account_control("TC", max(1, len(self.neighbors[node])), pkt.size_bytes)
            out.append(pkt)

        self._recompute(node, time_s)
        return out

    def _select_mpr(self, node: str) -> None:
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
            for n1, c in cover.items():
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

    def _recompute(self, node: str, time_s: float = 0.0) -> None:
        self._expire(node, time_s)
        graph: Dict[str, Set[str]] = defaultdict(set)
        for n in self.neighbors[node]:
            graph[node].add(n)
            graph[n].add(node)
        for origin, (neighs, _t0) in self.topology[node].items():
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
            for v in graph.get(u, []):
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
        topo_bytes = sum(len(s) * 8 for s, _t in self.topology[node].values())
        mpr_bytes = len(self.mpr[node]) * 8 + len(self.mpr_selectors[node]) * 8
        return (
            len(self.neighbors[node]) * 16
            + topo_bytes
            + len(self.routing_table[node]) * 24
            + mpr_bytes
            + 128
        )
