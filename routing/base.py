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

from .control_sizes import ControlSizeModel


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

    def __init__(self, nodes: List[str], size_model: Optional[ControlSizeModel] = None):
        self.nodes = set(nodes)
        self._control_sent = 0
        self._control_recv = 0
        self.size_model = size_model or ControlSizeModel()
        # Generation accounting (matches control message counts, fanout-based).
        self.control_bytes_generated = 0
        self.control_bytes_estimated_not_airtime = 0
        self.control_msgs_by_type: Dict[str, int] = {}
        self.control_bytes_by_type: Dict[str, int] = {}
        self.route_events: List[RouteEvent] = []

    def account_control(
        self,
        kind: str,
        count: int,
        size_bytes: int,
        *,
        airtime: bool = True,
    ) -> None:
        """Record logical control messages. Does not transmit them."""
        if count <= 0:
            return
        self._control_sent += count
        self.control_msgs_by_type[kind] = self.control_msgs_by_type.get(kind, 0) + count
        nbytes = count * int(size_bytes)
        self.control_bytes_by_type[kind] = self.control_bytes_by_type.get(kind, 0) + nbytes
        if airtime:
            self.control_bytes_generated += nbytes
        else:
            self.control_bytes_estimated_not_airtime += nbytes

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
