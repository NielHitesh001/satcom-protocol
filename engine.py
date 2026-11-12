"""
engine.py – Discrete-event simulation core.

Chronological event queue drives:
  - topology epochs
  - traffic injection
  - packet arrivals (propagation + serialization delay)
  - OLSR protocol ticks

Packet fates tracked separately: delivered, network_drop, residual.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from config import Scenario, load_scenario
from links import TopologySnapshot, build_ground_positions, compute_snapshot
from metrics import FlowMetrics, RunMetrics
from orbit import load_tles, propagate_positions
from routing import (
    AODVProtocol,
    OLSRProtocol,
    PredictiveProtocol,
    PracticalPredictiveProtocol,
    RoutingProtocol,
)
from routing.base import Packet, PacketType
from traffic import TrafficGenerator

_KIND_ORDER = {
    "topology": 0,
    "tick": 1,
    "inject": 2,
    "arrival": 3,
    "end": 9,
}


@dataclass(order=True)
class Event:
    time: float
    order: int
    seq: int
    kind: str = field(compare=False)
    node: str = field(default="", compare=False)
    packet: Optional[Packet] = field(default=None, compare=False)
    next_hop: str = field(default="", compare=False)
    snap_idx: int = field(default=-1, compare=False)


class Simulator:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.rng = np.random.default_rng(scenario.seed)
        self._seq = 0

        self.sats = load_tles(
            scenario.tle_path,
            max_sats=scenario.max_sats,
            name_filter=scenario.sat_subset,
        )
        if not self.sats:
            raise RuntimeError(f"No satellites loaded from {scenario.tle_path}")

        self.ground_pos = build_ground_positions(scenario.ground_sites)
        self.node_names = list(self.sats.keys()) + list(self.ground_pos.keys())

        self.start_dt = self._parse_time(scenario.start_time_utc)
        self.times: List[datetime] = []
        t = self.start_dt
        end = self.start_dt + timedelta(seconds=scenario.duration_s)
        while t <= end:
            self.times.append(t)
            t += timedelta(seconds=scenario.time_step_s)

        self.sat_pos_series = propagate_positions(self.sats, self.times)

        self.snapshots: List[TopologySnapshot] = []
        for i, _dt in enumerate(self.times):
            sat_pos = {n: self.sat_pos_series[n][i] for n in self.sats}
            snap = compute_snapshot(
                time_s=i * scenario.time_step_s,
                sat_positions=sat_pos,
                ground_positions=self.ground_pos,
                params=scenario.link,
            )
            self.snapshots.append(snap)

        self.protocol: RoutingProtocol = self._make_protocol()

        self.metrics = RunMetrics(
            scenario_name=scenario.name,
            protocol=scenario.protocol,
            seed=scenario.seed,
            tle_snapshot_id=scenario.tle_snapshot_id,
            duration_s=scenario.duration_s,
        )
        for f in scenario.traffic:
            self.metrics.flows[f.flow_id] = FlowMetrics(
                flow_id=f.flow_id, src=f.source, dst=f.destination
            )

        self.traffic_gen = TrafficGenerator(scenario.traffic, scenario.seed)
        self.traffic_events = self.traffic_gen.generate(scenario.duration_s)
        self.metrics.workload_fingerprint = self.traffic_gen.fingerprint(self.traffic_events)

        self._link_free_at: Dict[Tuple[str, str], float] = {}
        self.queue_limit = int(getattr(scenario.link, "queue_limit", 64) or 64)
        # Per-directed-link service-completion times.  This is the queue source
        # of truth; entries are discarded once their transmission has completed.
        self._link_finish_times: Dict[Tuple[str, str], List[float]] = {}

        self._event_q: List[Event] = []
        self._prev_adj: Dict[str, set] = {n: set() for n in self.node_names}

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _schedule(self, time: float, kind: str, **kwargs) -> None:
        ev = Event(
            time=time,
            order=_KIND_ORDER.get(kind, 5),
            seq=self._next_seq(),
            kind=kind,
            **kwargs,
        )
        heapq.heappush(self._event_q, ev)

    def _parse_time(self, s: str) -> datetime:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _make_protocol(self) -> RoutingProtocol:
        proto = self.scenario.protocol.lower()
        if proto == "aodv":
            return AODVProtocol(self.node_names)
        if proto == "olsr":
            return OLSRProtocol(self.node_names)
        if proto in ("predictive", "oracle", "predictive_oracle"):
            p = PredictiveProtocol(self.node_names)
            p.load_snapshots(self.snapshots)
            return p
        if proto in ("practical_predictive", "predictive_practical"):
            return PracticalPredictiveProtocol(self.node_names)
        raise ValueError(f"Unknown protocol: {proto}")

    def _snap_at(self, time_s: float) -> TopologySnapshot:
        idx = int(time_s / self.scenario.time_step_s)
        idx = max(0, min(idx, len(self.snapshots) - 1))
        while idx + 1 < len(self.snapshots) and self.snapshots[idx + 1].time_s <= time_s:
            idx += 1
        return self.snapshots[idx]

    def _edge_params(self, snap: TopologySnapshot, u: str, v: str) -> Tuple[float, float]:
        for e in snap.edges:
            if (e.u == u and e.v == v) or (e.v == u and e.u == v):
                return e.delay_s, e.capacity_mbps
        return 1000.0 / 299792.458, self.scenario.link.capacity_mbps

    def run(self) -> RunMetrics:
        duration = self.scenario.duration_s

        for i, snap in enumerate(self.snapshots):
            self._schedule(snap.time_s, "topology", snap_idx=i)
        for te in self.traffic_events:
            if te.time_s <= duration:
                self._schedule(te.time_s, "inject", packet=te.packet, node=te.packet.src)
        if isinstance(self.protocol, OLSRProtocol):
            tick_dt = min(self.scenario.time_step_s, self.protocol.hello_interval_s)
            t = 0.0
            while t <= duration:
                self._schedule(t, "tick")
                t += tick_dt
        self._schedule(duration, "end")

        max_events = 5_000_000
        n_events = 0

        while self._event_q and n_events < max_events:
            n_events += 1
            ev = heapq.heappop(self._event_q)
            if ev.time > duration + 1e-12 and ev.kind != "end":
                continue
            if ev.kind == "end":
                break
            if ev.kind == "topology":
                self._handle_topology(ev)
            elif ev.kind == "tick":
                self._handle_tick(ev.time)
            elif ev.kind == "inject":
                self._handle_inject(ev)
            elif ev.kind == "arrival":
                self._handle_arrival(ev)

        residual_packet_ids = set()
        while self._event_q:
            ev = heapq.heappop(self._event_q)
            if ev.kind == "arrival" and ev.packet and ev.packet.type == PacketType.DATA:
                fid = self._flow_id_for(ev.packet)
                if fid:
                    self.metrics.flows[fid].residual += 1
                    residual_packet_ids.add(ev.packet.pkt_id)

        # AODV DATA packets can be held locally while route discovery is pending.
        # They are no longer scheduled arrivals, but remain part of packet
        # conservation and must be reported as residual at the cutoff.
        if isinstance(self.protocol, AODVProtocol):
            for by_destination in self.protocol.buffer.values():
                for buffered in by_destination.values():
                    for pkt in buffered:
                        if pkt.pkt_id not in residual_packet_ids:
                            fid = self._flow_id_for(pkt)
                            if fid:
                                self.metrics.flows[fid].residual += 1
                                residual_packet_ids.add(pkt.pkt_id)

        self.metrics.control_messages_sent = self.protocol.control_messages_sent()
        self.metrics.control_messages_recv = self.protocol.control_messages_received()
        self.metrics.route_changes = sum(
            1 for e in self.protocol.route_events if e.event_type == "route_change"
        )
        starts = {
            (e.node, e.destination): e.time_s
            for e in self.protocol.route_events
            if e.event_type == "discovery_start"
        }
        for e in self.protocol.route_events:
            if e.event_type == "discovery_end" and e.detail == "route installed":
                key = (e.node, e.destination)
                if key in starts:
                    dt = e.time_s - starts[key]
                    if dt >= 0:
                        self.metrics.discovery_times_s.append(dt)

        self.metrics.events_processed = n_events
        return self.metrics

    def _handle_topology(self, ev: Event) -> None:
        snap = self.snapshots[ev.snap_idx]
        t = snap.time_s
        for n in self.node_names:
            old = self._prev_adj.get(n, set())
            new = snap.adj.get(n, set())
            for lost in sorted(old - new):
                self.protocol.on_link_change(t, n, lost, up=False)
            for gained in sorted(new - old):
                self.protocol.on_link_change(t, n, gained, up=True)
        self._prev_adj = {n: set(snap.adj.get(n, set())) for n in self.node_names}
        if isinstance(self.protocol, PracticalPredictiveProtocol):
            self.protocol.update_from_snapshot(snap, t)
        for n in self.node_names:
            sz = self.protocol.state_size(n)
            if sz > self.metrics.peak_state_bytes.get(n, 0):
                self.metrics.peak_state_bytes[n] = sz

    def _handle_tick(self, time_s: float) -> None:
        if not isinstance(self.protocol, OLSRProtocol):
            return
        snap = self._snap_at(time_s)
        for n in self.node_names:
            for ctrl in self.protocol.tick(time_s, n):
                self._send(time_s, n, "__broadcast__", ctrl, snap)
        self._drain_outbound(time_s, snap)

    def _handle_inject(self, ev: Event) -> None:
        pkt = ev.packet
        if pkt is None:
            return
        t = ev.time
        snap = self._snap_at(t)
        flow_id = self._flow_id_for(pkt)
        if flow_id:
            self.metrics.flows[flow_id].sent += 1
        nh = self.protocol.on_packet(t, pkt.src, pkt)
        if nh is None:
            if pkt.src == pkt.dst and flow_id:
                self.metrics.flows[flow_id].delivered += 1
                self.metrics.flows[flow_id].latencies_s.append(0.0)
            elif flow_id and not self._is_buffered(pkt.src, pkt):
                self.metrics.flows[flow_id].dropped += 1
        else:
            self._send(t, pkt.src, nh, pkt, snap)
        self._drain_outbound(t, snap)

    def _handle_arrival(self, ev: Event) -> None:
        pkt = ev.packet
        node = ev.node
        if pkt is None:
            return
        t = ev.time
        snap = self._snap_at(t)
        nh = self.protocol.on_packet(t, node, pkt)
        flow_id = self._flow_id_for(pkt)
        if nh is None:
            if node == pkt.dst:
                if flow_id and pkt.type == PacketType.DATA:
                    fm = self.metrics.flows[flow_id]
                    fm.delivered += 1
                    fm.latencies_s.append(t - pkt.creation_time_s)
                    fm.hop_counts.append(max(0, len(pkt.path) - 1))
            elif flow_id and pkt.type == PacketType.DATA:
                if not self._is_buffered(node, pkt):
                    self.metrics.flows[flow_id].dropped += 1
        else:
            self._send(t, node, nh, pkt, snap)
        self._drain_outbound(t, snap)

    def _is_buffered(self, node: str, pkt: Packet) -> bool:
        if not isinstance(self.protocol, AODVProtocol):
            return False
        buf = self.protocol.buffer.get(node, {}).get(pkt.dst, [])
        return any(p.pkt_id == pkt.pkt_id for p in buf)

    def _drain_outbound(self, now: float, snap: TopologySnapshot) -> None:
        if not isinstance(self.protocol, AODVProtocol):
            return
        for ob in self.protocol.pop_outbound():
            self._send(now, ob.node, ob.next_hop or "__broadcast__", ob.packet, snap)

    def _reserve_link(
        self, now: float, src: str, dst: str, pkt: Packet, capacity_mbps: float
    ) -> Optional[Tuple[float, float]]:
        """Reserve directed-link serialization capacity or reject a full DATA queue."""
        key = (src, dst)
        finishes = self._link_finish_times.setdefault(key, [])
        while finishes and finishes[0] <= now:
            heapq.heappop(finishes)

        serialization_s = (pkt.size_bytes * 8.0) / (capacity_mbps * 1e6) if capacity_mbps > 0 else 0.0
        start = max(now, self._link_free_at.get(key, now))
        if pkt.type == PacketType.DATA and len(finishes) >= self.queue_limit:
            return None

        finish = start + serialization_s
        heapq.heappush(finishes, finish)
        self._link_free_at[key] = finish
        return start, serialization_s

    def _send(
        self, now: float, src: str, nh: str, pkt: Packet, snap: TopologySnapshot
    ) -> None:
        loss_p = float(self.scenario.link.loss_prob)

        if nh == "__broadcast__":
            for neigh in sorted(snap.adj.get(src, [])):
                if loss_p > 0 and self.rng.random() < loss_p:
                    if pkt.type == PacketType.DATA:
                        fid = self._flow_id_for(pkt)
                        if fid:
                            self.metrics.flows[fid].dropped += 1
                    continue
                delay, cap = self._edge_params(snap, src, neigh)
                reservation = self._reserve_link(now, src, neigh, pkt, cap)
                if reservation is None:
                    if pkt.type == PacketType.DATA:
                        fid = self._flow_id_for(pkt)
                        if fid:
                            self.metrics.flows[fid].dropped += 1
                    continue
                start, ser = reservation
                arr = start + ser + delay
                new_pkt = Packet(
                    pkt_id=pkt.pkt_id,
                    src=pkt.src,
                    dst=pkt.dst,
                    type=pkt.type,
                    size_bytes=pkt.size_bytes,
                    creation_time_s=pkt.creation_time_s,
                    ttl=pkt.ttl,
                    payload=pkt.payload,
                    path=list(pkt.path) + [src],
                )
                self._schedule(arr, "arrival", node=neigh, packet=new_pkt)
            return

        if nh not in snap.adj.get(src, set()):
            if pkt.type == PacketType.DATA:
                fid = self._flow_id_for(pkt)
                if fid:
                    self.metrics.flows[fid].dropped += 1
            return

        if loss_p > 0 and self.rng.random() < loss_p:
            if pkt.type == PacketType.DATA:
                fid = self._flow_id_for(pkt)
                if fid:
                    self.metrics.flows[fid].dropped += 1
            return

        delay, cap = self._edge_params(snap, src, nh)
        reservation = self._reserve_link(now, src, nh, pkt, cap)
        if reservation is None:
            if pkt.type == PacketType.DATA:
                fid = self._flow_id_for(pkt)
                if fid:
                    self.metrics.flows[fid].dropped += 1
            return
        start, ser = reservation
        arr = start + ser + delay
        pkt.path = list(pkt.path) + [src]
        self._schedule(arr, "arrival", node=nh, packet=pkt)

    def _flow_id_for(self, pkt: Packet) -> Optional[str]:
        for f in self.scenario.traffic:
            if f.source == pkt.src and f.destination == pkt.dst:
                return f.flow_id
        return None


def run_scenario(
    scenario_path: str | Path, output_dir: str | Path = "outputs"
) -> RunMetrics:
    scenario_path = Path(scenario_path).resolve()
    scenario = load_scenario(scenario_path)

    tle = Path(scenario.tle_path)
    if not tle.is_absolute():
        candidate = scenario_path.parent / tle
        if not candidate.exists():
            candidate = scenario_path.parent.parent / tle
        scenario.tle_path = str(candidate.resolve())

    sim = Simulator(scenario)
    metrics = sim.run()

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = f"{scenario.name}_{scenario.protocol}_{scenario.seed}"
    metrics.to_csv(out / f"{stamp}_flows.csv")
    metrics.write_manifest(out / f"{stamp}_manifest.json")
    return metrics
