"""
traffic.py – Generate application flows between nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import List

import numpy as np

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
        self.rng = np.random.default_rng(seed)

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
            arrival_model = flow.arrival_model.lower()
            if arrival_model not in {"deterministic", "poisson"}:
                raise ValueError(f"Unsupported arrival_model: {flow.arrival_model}")
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
                if arrival_model == "poisson":
                    t += float(self.rng.exponential(interval))
                else:
                    t += interval
        events.sort(key=lambda e: (e.time_s, e.packet.pkt_id))
        return events

    @staticmethod
    def fingerprint(events: List[GeneratedPacket]) -> str:
        """Stable digest of the actual workload injected into a simulation."""
        rows = [
            (round(event.time_s, 12), event.packet.src, event.packet.dst,
             event.packet.size_bytes, event.packet.pkt_id)
            for event in events
        ]
        blob = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()
