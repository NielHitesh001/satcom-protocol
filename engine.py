"""Chronological discrete-event execution engine for SATCOM routing scenarios."""

from __future__ import annotations

import copy
import heapq
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from config import Scenario, load_scenario
from links import TopologySnapshot, build_ground_positions, compute_snapshot
from metrics import FlowMetrics, RunMetrics
from orbit import load_tles, propagate_positions
from routing.aodv import AODVProtocol
from routing.base import Packet, PacketType, RoutingProtocol
from routing.control_sizes import ControlSizeModel, serialization_s
from routing.olsr import OLSRProtocol
from routing.practical_predictive import PracticalPredictiveProtocol
from routing.predictive import PredictiveProtocol
from traffic import TrafficGenerator


_KIND_ORDER = {
    "topology": 0,
    "tx_done": 0,
    "tick": 1,
    "aodv_retry": 1,
    "inject": 2,
    "arrival": 3,
    "end": 9,
}
_MAX_EVENTS = 5_000_000


@dataclass(order=True)
class Event:
    """An event ordered deterministically by time, kind priority, and sequence."""

    time: float
    order: int
    seq: int
    kind: str = field(compare=False)
    node: Optional[str] = field(default=None, compare=False)
    packet: Optional[Packet] = field(default=None, compare=False)
    payload: Any = field(default=None, compare=False)


class Simulator:
    """Execute one scenario using a time-ordered, single-threaded event queue."""

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self._rng = random.Random(scenario.seed)
        self._event_q: List[Event] = []
        self._sequence = 0
        self._active_edges: Set[Tuple[str, str]] = set()
        self._current_snapshot: Optional[TopologySnapshot] = None
        self._link_free_at: Dict[Tuple[str, str], float] = {}
        self._data_queue_depth: Dict[Tuple[str, str], int] = {}
        self.queue_limit = max(0, int(scenario.link.queue_limit))
        self._packet_flow: Dict[int, str] = {}
        self._packet_status: Dict[int, str] = {}

        self.times_s = self._snapshot_times()
        self.start_utc = self._parse_time(scenario.start_time_utc)
        tle_path = self._resolve_tle_path(scenario.tle_path)
        self.satellites = load_tles(tle_path, max_sats=scenario.max_sats, name_filter=scenario.sat_subset)
        if not self.satellites:
            raise ValueError(f"No usable satellites loaded from {tle_path}")
        positions = propagate_positions(
            self.satellites,
            [self.start_utc + timedelta(seconds=t) for t in self.times_s],
        )
        self.ground_positions = build_ground_positions(scenario.ground_sites)
        self.snapshots = [
            compute_snapshot(
                t,
                {name: pos[index] for name, pos in positions.items()},
                self.ground_positions,
                scenario.link,
            )
            for index, t in enumerate(self.times_s)
        ]
        self.nodes = list(self.satellites) + list(self.ground_positions)
        self.size_model = ControlSizeModel.from_dict(scenario.control_sizes)
        self.protocol = self._make_protocol()
        self.metrics = RunMetrics(
            scenario_name=scenario.name,
            protocol=scenario.protocol,
            seed=scenario.seed,
            tle_snapshot_id=scenario.tle_snapshot_id,
            duration_s=scenario.duration_s,
            control_size_model=self.size_model.as_dict(),
        )
        for flow in scenario.traffic:
            self.metrics.flows[flow.flow_id] = FlowMetrics(flow.flow_id, flow.source, flow.destination)
        self._schedule_initial_events()

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid start_time_utc: {value!r}") from exc
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)

    @staticmethod
    def _resolve_tle_path(value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        candidates = (Path.cwd() / path, Path(__file__).resolve().parent / path)
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        return candidates[0]

    def _snapshot_times(self) -> List[float]:
        if self.scenario.duration_s < 0 or self.scenario.time_step_s <= 0:
            raise ValueError("duration_s must be non-negative and time_step_s must be positive")
        step = self.scenario.time_step_s
        count = int(self.scenario.duration_s / step)
        return [i * step for i in range(count + 1)]

    def _make_protocol(self) -> RoutingProtocol:
        name = self.scenario.protocol.lower()
        if name in {"predictive", "oracle"}:
            protocol = PredictiveProtocol(self.nodes)
            protocol.load_snapshots(self.snapshots)
            return protocol
        if name == "practical_predictive":
            return PracticalPredictiveProtocol(
                self.nodes,
                recompute_interval_s=self.scenario.time_step_s,
                max_range_km=self.scenario.link.max_range_km,
                size_model=self.size_model,
            )
        if name == "aodv":
            return AODVProtocol(self.nodes, size_model=self.size_model)
        if name == "olsr":
            return OLSRProtocol(self.nodes, size_model=self.size_model)
        raise ValueError(f"Unsupported routing protocol: {self.scenario.protocol!r}")

    def _push(self, time_s: float, kind: str, *, node: Optional[str] = None,
              packet: Optional[Packet] = None, payload: Any = None) -> None:
        if kind not in _KIND_ORDER:
            raise ValueError(f"Unknown event kind: {kind}")
        self._sequence += 1
        heapq.heappush(self._event_q, Event(time_s, _KIND_ORDER[kind], self._sequence, kind, node, packet, payload))

    def _schedule_initial_events(self) -> None:
        for snapshot in self.snapshots:
            self._push(snapshot.time_s, "topology", payload=snapshot)
        for generated in TrafficGenerator(self.scenario.traffic, self.scenario.seed).generate(self.scenario.duration_s):
            flow_id = self._flow_id_for_packet(generated.packet)
            self._push(generated.time_s, "inject", packet=generated.packet, payload=flow_id)
        if isinstance(self.protocol, OLSRProtocol):
            interval = min(self.scenario.time_step_s, self.protocol.hello_interval_s)
            tick = 0.0
            while tick <= self.scenario.duration_s + 1e-9:
                for node in self.nodes:
                    self._push(tick, "tick", node=node)
                tick += interval
        self._push(self.scenario.duration_s, "end")

    def _flow_id_for_packet(self, packet: Packet) -> Optional[str]:
        for flow in self.scenario.traffic:
            if flow.source == packet.src and flow.destination == packet.dst:
                return flow.flow_id
        return None

    @staticmethod
    def _edge_key(u: str, v: str) -> Tuple[str, str]:
        return (u, v) if u < v else (v, u)

    def _edge_params(self, snapshot: TopologySnapshot, u: str, v: str) -> Tuple[float, float]:
        edge = snapshot.edge_index.get(self._edge_key(u, v))
        if edge is None:
            raise KeyError(f"No active link {u}->{v} at t={snapshot.time_s}")
        return edge.delay_s, edge.capacity_mbps

    def _apply_topology(self, time_s: float, snapshot: TopologySnapshot) -> None:
        new_edges = {self._edge_key(edge.u, edge.v) for edge in snapshot.edges}
        for u, v in sorted(self._active_edges - new_edges):
            self.protocol.on_link_change(time_s, u, v, False)
        for u, v in sorted(new_edges - self._active_edges):
            self.protocol.on_link_change(time_s, u, v, True)
        self._active_edges = new_edges
        self._current_snapshot = snapshot
        if isinstance(self.protocol, PracticalPredictiveProtocol):
            self.protocol.update_from_snapshot(snapshot, time_s)
        self._drain_protocol_outbound(time_s, snapshot)

    def _copy_for_hop(self, packet: Packet, source: str) -> Packet:
        forwarded = copy.deepcopy(packet)
        forwarded.path.append(source)
        if forwarded.type == PacketType.DATA:
            forwarded.ttl -= 1
        return forwarded

    def _send(self, time_s: float, source: str, next_hop: Optional[str], packet: Packet,
              snapshot: TopologySnapshot) -> None:
        if next_hop is None:
            if packet.type == PacketType.DATA:
                self._drop_data(packet)
            return
        if next_hop == "__broadcast__":
            for neighbour in sorted(snapshot.adj.get(source, ())):
                self._send(time_s, source, neighbour, packet, snapshot)
            return
        if next_hop not in snapshot.adj.get(source, set()):
            if packet.type == PacketType.DATA:
                self._drop_data(packet)
            return
        if packet.type == PacketType.DATA and packet.ttl <= 0:
            self._drop_data(packet)
            return

        try:
            propagation, capacity = self._edge_params(snapshot, source, next_hop)
        except KeyError:
            if packet.type == PacketType.DATA:
                self._drop_data(packet)
            return
        if capacity <= 0:
            if packet.type == PacketType.DATA:
                self._drop_data(packet)
            return

        key = (source, next_hop)
        start_tx = max(time_s, self._link_free_at.get(key, time_s))
        is_data = packet.type == PacketType.DATA
        if is_data and start_tx > time_s and self._data_queue_depth.get(key, 0) >= self.queue_limit:
            self._drop_data(packet)
            return

        if self._rng.random() < self.scenario.link.loss_prob:
            if is_data:
                self._drop_data(packet)
            else:
                self.metrics.control_loss_tx += 1
            return
        ser = serialization_s(packet.size_bytes, capacity)
        self._link_free_at[key] = start_tx + ser
        if is_data:
            self._data_queue_depth[key] = self._data_queue_depth.get(key, 0) + 1
            self._push(start_tx + ser, "tx_done", payload=key)
        else:
            self.metrics.control_airtime_bytes += packet.size_bytes
            self.metrics.control_airtime_tx += 1
            self.metrics.control_serialization_s += ser
        self._push(start_tx + ser + propagation, "arrival", node=next_hop,
                   packet=self._copy_for_hop(packet, source))

    def _drain_protocol_outbound(self, time_s: float, snapshot: TopologySnapshot) -> None:
        if not isinstance(self.protocol, AODVProtocol):
            return
        while True:
            outbound = self.protocol.pop_outbound()
            if not outbound:
                return
            for item in outbound:
                self._send(time_s, item.node, item.next_hop, item.packet, snapshot)

    def _schedule_new_aodv_retries(self, time_s: float, node: str,
                                   pending_before: Set[str]) -> None:
        """Schedule one timeout for each discovery created by this event."""
        if not isinstance(self.protocol, AODVProtocol):
            return
        for dest in set(self.protocol.pending[node]) - pending_before:
            self._push(
                time_s + self.protocol.rreq_retry_timeout_s,
                "aodv_retry",
                node=node,
                payload=dest,
            )

    def _is_buffered(self, packet: Packet) -> bool:
        if not isinstance(self.protocol, AODVProtocol):
            return False
        return any(packet in queue for destinations in self.protocol.buffer.values() for queue in destinations.values())

    def _flow_for(self, packet: Packet) -> Optional[FlowMetrics]:
        flow_id = self._packet_flow.get(packet.pkt_id) or self._flow_id_for_packet(packet)
        return self.metrics.flows.get(flow_id) if flow_id else None

    def _drop_data(self, packet: Packet) -> None:
        if self._packet_status.get(packet.pkt_id) in {"delivered", "dropped", "residual"}:
            return
        flow = self._flow_for(packet)
        if flow is not None:
            flow.dropped += 1
        self._packet_status[packet.pkt_id] = "dropped"

    def _deliver_data(self, time_s: float, packet: Packet) -> None:
        if self._packet_status.get(packet.pkt_id) in {"delivered", "dropped", "residual"}:
            return
        flow = self._flow_for(packet)
        if flow is not None:
            flow.delivered += 1
            flow.latencies_s.append(max(0.0, time_s - packet.creation_time_s))
            flow.hop_counts.append(max(0, len(packet.path) - 1))
            self.metrics.data_bytes_delivered += packet.size_bytes
        self._packet_status[packet.pkt_id] = "delivered"

    def _handle_inject(self, time_s: float, packet: Packet, flow_id: Optional[str]) -> None:
        if flow_id is None or flow_id not in self.metrics.flows:
            return
        self._packet_flow[packet.pkt_id] = flow_id
        self._packet_status[packet.pkt_id] = "pending"
        self.metrics.flows[flow_id].sent += 1
        self.metrics.data_bytes_injected += packet.size_bytes
        if packet.src == packet.dst:
            self._deliver_data(time_s, packet)
            return
        if self._current_snapshot is None:
            self._drop_data(packet)
            return
        pending_before = set(self.protocol.pending[packet.src]) if isinstance(self.protocol, AODVProtocol) else set()
        next_hop = self.protocol.on_packet(time_s, packet.src, packet)
        self._drain_protocol_outbound(time_s, self._current_snapshot)
        self._schedule_new_aodv_retries(time_s, packet.src, pending_before)
        if next_hop is not None:
            self._send(time_s, packet.src, next_hop, packet, self._current_snapshot)
        elif not self._is_buffered(packet):
            self._drop_data(packet)

    def _handle_arrival(self, time_s: float, node: str, packet: Packet) -> None:
        if packet.type == PacketType.DATA and node == packet.dst:
            self._deliver_data(time_s, packet)
            return
        if self._current_snapshot is None:
            if packet.type == PacketType.DATA:
                self._drop_data(packet)
            return
        pending_before = set(self.protocol.pending[node]) if isinstance(self.protocol, AODVProtocol) else set()
        next_hop = self.protocol.on_packet(time_s, node, packet)
        self._drain_protocol_outbound(time_s, self._current_snapshot)
        self._schedule_new_aodv_retries(time_s, node, pending_before)
        if next_hop is not None:
            self._send(time_s, node, next_hop, packet, self._current_snapshot)
        elif packet.type == PacketType.DATA and not self._is_buffered(packet):
            self._drop_data(packet)

    def _record_protocol_metrics(self, *, final: bool = False) -> None:
        self.metrics.control_messages_sent = self.protocol.control_messages_sent()
        self.metrics.control_messages_recv = self.protocol.control_messages_received()
        self.metrics.control_bytes_sent = self.protocol.control_bytes_generated
        self.metrics.control_bytes_estimated_not_airtime = self.protocol.control_bytes_estimated_not_airtime
        self.metrics.control_msgs_by_type = dict(self.protocol.control_msgs_by_type)
        self.metrics.control_bytes_by_type = dict(self.protocol.control_bytes_by_type)
        self.metrics.route_changes = sum(1 for event in self.protocol.route_events if event.event_type == "route_change")
        if final:
            starts: Dict[Tuple[str, str], float] = {}
            for event in self.protocol.route_events:
                key = (event.node, event.destination)
                if event.event_type == "discovery_start":
                    starts[key] = event.time_s
                elif event.event_type == "discovery_end" and key in starts:
                    self.metrics.discovery_times_s.append(event.time_s - starts[key])
        for node in self.nodes:
            self.metrics.peak_state_bytes[node] = max(
                self.metrics.peak_state_bytes.get(node, 0), self.protocol.state_size(node)
            )

    def _finalize(self) -> None:
        for packet_id, status in list(self._packet_status.items()):
            if status != "pending":
                continue
            flow_id = self._packet_flow.get(packet_id)
            flow = self.metrics.flows.get(flow_id) if flow_id else None
            if flow is not None:
                flow.residual += 1
            self._packet_status[packet_id] = "residual"
        self._record_protocol_metrics(final=True)
        for flow in self.metrics.flows.values():
            if flow.sent != flow.delivered + flow.dropped + flow.residual:
                raise RuntimeError(f"Packet accounting invariant failed for flow {flow.flow_id}")

    def run(self) -> RunMetrics:
        while self._event_q:
            event = heapq.heappop(self._event_q)
            if event.time > self.scenario.duration_s + 1e-9:
                break
            self.metrics.events_processed += 1
            if self.metrics.events_processed > _MAX_EVENTS:
                raise RuntimeError(f"Exceeded {_MAX_EVENTS:,} events")
            if event.kind == "topology":
                self._apply_topology(event.time, event.payload)
            elif event.kind == "tx_done":
                key = event.payload
                self._data_queue_depth[key] = max(0, self._data_queue_depth.get(key, 0) - 1)
            elif event.kind == "tick" and isinstance(self.protocol, OLSRProtocol):
                for packet in self.protocol.tick(event.time, event.node):
                    self._send(event.time, event.node, "__broadcast__", packet, self._current_snapshot)
            elif event.kind == "aodv_retry" and isinstance(self.protocol, AODVProtocol):
                failed = self.protocol.retry_discovery(event.time, event.node, event.payload)
                self._drain_protocol_outbound(event.time, self._current_snapshot)
                if event.payload in self.protocol.pending[event.node]:
                    self._push(
                        event.time + self.protocol.rreq_retry_timeout_s,
                        "aodv_retry",
                        node=event.node,
                        payload=event.payload,
                    )
                for packet in failed:
                    self._drop_data(packet)
            elif event.kind == "inject":
                self._handle_inject(event.time, event.packet, event.payload)
            elif event.kind == "arrival":
                self._handle_arrival(event.time, event.node, event.packet)
            elif event.kind == "end":
                break
            self._record_protocol_metrics()
        self._finalize()
        return self.metrics


def run_scenario(path: str | Path, output_dir: str | Path | None = None) -> RunMetrics:
    """Load, execute, and optionally write the standard run artefacts."""
    scenario = load_scenario(path)
    if not Path(scenario.tle_path).is_absolute():
        candidate = Path(path).resolve().parent / scenario.tle_path
        if candidate.exists():
            scenario.tle_path = str(candidate)
    metrics = Simulator(scenario).run()
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        stem = f"{scenario.name}_{scenario.protocol}_{scenario.seed}"
        metrics.to_csv(out / f"{stem}_flows.csv")
        metrics.write_manifest(out / f"{stem}_manifest.json")
    return metrics
