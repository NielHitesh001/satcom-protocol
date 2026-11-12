"""
predictive.py – Oracle / predictive baseline that uses the known future topology.

Because the orbital motion is deterministic, the simulator can pre-compute
shortest paths for every time window. This serves as a lower-bound reference
for latency and delivery ratio.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

import heapq

from .base import Packet, PacketType, RoutingProtocol
from links import TopologySnapshot


class PredictiveProtocol(RoutingProtocol):
    """
    Pre-computes shortest-path next hops from a sequence of topology snapshots.
    The engine supplies the snapshots before the run starts.
    """

    def __init__(self, nodes: List[str]):
        super().__init__(nodes)
        # time_s -> node -> dest -> next_hop
        self.schedules: Dict[float, Dict[str, Dict[str, str]]] = {}
        self.neighbors: Dict[str, Set[str]] = {n: set() for n in nodes}
        self._current_time = 0.0

    def load_snapshots(self, snapshots: List[TopologySnapshot]) -> None:
        """Pre-compute routes for every snapshot."""
        for snap in snapshots:
            self.schedules[snap.time_s] = self._compute_all_pairs(snap)

    def _compute_all_pairs(self, snap: TopologySnapshot) -> Dict[str, Dict[str, str]]:
        # Build adjacency
        graph: Dict[str, Set[str]] = defaultdict(set)
        for e in snap.edges:
            graph[e.u].add(e.v)
            graph[e.v].add(e.u)

        result: Dict[str, Dict[str, str]] = {}
        for src in self.nodes:
            result[src] = self._dijkstra_next(src, graph)
        return result

    def _dijkstra_next(self, src: str, graph: Dict[str, Set[str]]) -> Dict[str, str]:
        dist = {src: 0}
        prev: Dict[str, str] = {}
        pq = [(0, src)]
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
            if dest == src:
                continue
            cur = dest
            while prev.get(cur) != src and cur in prev:
                cur = prev[cur]
            if prev.get(cur) == src:
                table[dest] = cur
        return table

    def on_link_change(self, time_s: float, u: str, v: str, up: bool) -> None:
        if up:
            self.neighbors[u].add(v)
            self.neighbors[v].add(u)
        else:
            self.neighbors[u].discard(v)
            self.neighbors[v].discard(u)
        self._current_time = time_s

    def next_hop(self, node: str, destination: str, time_s: float) -> Optional[str]:
        # Find the most recent schedule at or before time_s
        times = sorted(t for t in self.schedules if t <= time_s)
        if not times:
            return None
        sched = self.schedules[times[-1]]
        return sched.get(node, {}).get(destination)

    def on_packet(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        if packet.type != PacketType.DATA:
            return None
        if node == packet.dst:
            return None
        return self.next_hop(node, packet.dst, time_s)

    def state_size(self, node: str) -> int:
        # Predictive node only needs the current next-hop table
        # (in a real system this would be uploaded from the ground)
        return 256  # fixed small table
