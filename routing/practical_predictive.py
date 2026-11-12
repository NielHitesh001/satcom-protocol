"""
practical_predictive.py – Deployable-style predictive routing (NOT an oracle).

Uses only information available at the current time:
  - Current topology
  - Configurable prediction horizon / recompute interval
  - Models schedule distribution as control cost

Unlike PredictiveProtocol (oracle), this does not use future snapshots.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

import heapq

from .base import Packet, PacketType, RoutingProtocol
from links import TopologySnapshot


class PracticalPredictiveProtocol(RoutingProtocol):
    def __init__(
        self,
        nodes: List[str],
        horizon_s: float = 30.0,
        recompute_interval_s: float = 10.0,
    ):
        super().__init__(nodes)
        self.horizon_s = horizon_s
        self.recompute_interval_s = recompute_interval_s
        self.neighbors: Dict[str, Set[str]] = {n: set() for n in nodes}
        self.routing_table: Dict[str, Dict[str, str]] = {n: {} for n in nodes}
        self._edge_weight: Dict[Tuple[str, str], float] = {}
        self._last_recompute = -1e9
        self._graph: Dict[str, Set[str]] = defaultdict(set)

    def on_link_change(self, time_s: float, u: str, v: str, up: bool) -> None:
        if up:
            self.neighbors[u].add(v)
            self.neighbors[v].add(u)
            self._graph[u].add(v)
            self._graph[v].add(u)
            self._edge_weight[(u, v)] = 1.0
            self._edge_weight[(v, u)] = 1.0
        else:
            self.neighbors[u].discard(v)
            self.neighbors[v].discard(u)
            self._graph[u].discard(v)
            self._graph[v].discard(u)
            self._edge_weight.pop((u, v), None)
            self._edge_weight.pop((v, u), None)
        self._recompute_all()
        self._control_sent += 1

    def update_from_snapshot(self, snap: TopologySnapshot, time_s: float) -> None:
        self._graph = defaultdict(set)
        self.neighbors = {n: set() for n in self.nodes}
        for e in snap.edges:
            self._graph[e.u].add(e.v)
            self._graph[e.v].add(e.u)
            self.neighbors[e.u].add(e.v)
            self.neighbors[e.v].add(e.u)
            w = 1.0 + e.distance_km / 10000.0
            self._edge_weight[(e.u, e.v)] = w
            self._edge_weight[(e.v, e.u)] = w
        if time_s - self._last_recompute >= self.recompute_interval_s:
            self._recompute_all()
            self._last_recompute = time_s
            self._control_sent += len(self.nodes)

    def _recompute_all(self) -> None:
        for src in self.nodes:
            self.routing_table[src] = self._dijkstra(src)

    def _dijkstra(self, src: str) -> Dict[str, str]:
        dist = {src: 0.0}
        prev: Dict[str, str] = {}
        pq = [(0.0, src)]
        visited: Set[str] = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            for v in self._graph.get(u, []):
                w = self._edge_weight.get((u, v), 1.0)
                nd = d + w
                if v not in dist or nd < dist[v]:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        table: Dict[str, str] = {}
        for dest in dist:
            if dest == src:
                continue
            cur = dest
            while prev.get(cur) != src and cur in prev:
                cur = prev[cur]
            if prev.get(cur) == src:
                table[dest] = cur
        return table

    def next_hop(self, node: str, destination: str, time_s: float) -> Optional[str]:
        return self.routing_table.get(node, {}).get(destination)

    def on_packet(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        if packet.type != PacketType.DATA:
            return None
        if node == packet.dst:
            return None
        return self.next_hop(node, packet.dst, time_s)

    def state_size(self, node: str) -> int:
        return len(self.routing_table.get(node, {})) * 24 + 128
