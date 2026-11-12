"""
base.py – Common routing protocol interface.

Every protocol must implement:
  on_link_change(event)
  on_packet(node, packet)
  next_hop(node, destination, time)
  control_messages_sent()
  state_size(node)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set


class PacketType(Enum):
    DATA = auto()
    RREQ = auto()          # AODV
    RREP = auto()
    RERR = auto()
    HELLO = auto()         # OLSR
    TC = auto()            # Topology Control
    OTHER = auto()


@dataclass
class Packet:
    pkt_id: int
    src: str
    dst: str
    type: PacketType
    size_bytes: int
    creation_time_s: float
    ttl: int = 32
    payload: Any = None
    path: List[str] = field(default_factory=list)  # for debugging / hop count


@dataclass
class RouteEvent:
    time_s: float
    event_type: str          # "discovery_start", "discovery_end", "route_change", "route_fail"
    node: str
    destination: str
    detail: str = ""


class RoutingProtocol(ABC):
    """Abstract base class for all routing protocols."""

    def __init__(self, nodes: List[str]):
        # Stable node order prevents hash randomization from changing ties.\n        self.nodes = tuple(sorted(nodes))
        self._control_sent = 0
        self._control_recv = 0
        self.route_events: List[RouteEvent] = []

    @abstractmethod
    def on_link_change(self, time_s: float, u: str, v: str, up: bool) -> None:
        """Called when a bidirectional link appears or disappears."""
        ...

    @abstractmethod
    def on_packet(self, time_s: float, node: str, packet: Packet) -> Optional[str]:
        """
        Handle a packet that has arrived at `node`.
        Return the next-hop node name, or None if the packet is dropped / delivered.
        """
        ...

    @abstractmethod
    def next_hop(self, node: str, destination: str, time_s: float) -> Optional[str]:
        """Return the next hop toward destination, or None if no route."""
        ...

    def control_messages_sent(self) -> int:
        return self._control_sent

    def control_messages_received(self) -> int:
        return self._control_recv

    @abstractmethod
    def state_size(self, node: str) -> int:
        """Approximate bytes of routing state held by `node`."""
        ...

    def record_event(self, time_s: float, etype: str, node: str, dest: str, detail: str = "") -> None:
        self.route_events.append(RouteEvent(time_s, etype, node, dest, detail))
