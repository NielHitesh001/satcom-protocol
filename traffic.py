"""
traffic.py – Generate application flows between nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from config import TrafficFlow
from routing.base import Packet, PacketType


@dataclass
class GeneratedPacket:
    time_s: float
    packet: Packet


class TrafficGenerator:
    def __init__(self, flows: List[TrafficFlow], seed: int = 42):
        self.flows = flows
        self.seed = seed
        self._pkt_id = 0

    def _next_id(self) -> int:
        self._pkt_id += 1
        return self._pkt_id

    def generate(self, sim_duration_s: float) -> List[GeneratedPacket]:
        """Produce a list of (injection_time, Packet) for the whole run."""
        events: List[GeneratedPacket] = []
        for flow in self.flows:
            t = flow.start_s
            end = min(flow.start_s + flow.duration_s, sim_duration_s)
            interval = 1.0 / flow.rate_pps if flow.rate_pps > 0 else 1.0
            while t < end:
                pkt = Packet(
                    pkt_id=self._next_id(),
                    src=flow.source,
                    dst=flow.destination,
                    type=PacketType.DATA,
                    size_bytes=flow.packet_size_bytes,
                    creation_time_s=t,
                    path=[flow.source],
                )
                events.append(GeneratedPacket(time_s=t, packet=pkt))
                t += interval
        events.sort(key=lambda e: e.time_s)
        return events
