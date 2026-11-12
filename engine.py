"""
engine.py – Discrete-event simulation core.

Advances time, applies topology changes, injects traffic, and drives
the chosen routing protocol.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from config import Scenario, load_scenario
from links import TopologySnapshot, build_ground_positions, compute_snapshot
from metrics import FlowMetrics, RunMetrics
from orbit import load_tles, propagate_positions
from routing import AODVProtocol, OLSRProtocol, PredictiveProtocol, RoutingProtocol
from routing.base import Packet, PacketType
from traffic import TrafficGenerator


class Simulator:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.rng = np.random.default_rng(scenario.seed)

        # Load satellites
        self.sats = load_tles(
            scenario.tle_path,
            max_sats=scenario.max_sats,
            name_filter=scenario.sat_subset,
        )
        if not self.sats:
            raise RuntimeError(f"No satellites loaded from {scenario.tle_path}")

        self.ground_pos = build_ground_positions(scenario.ground_sites)
        self.node_names = list(self.sats.keys()) + list(self.ground_pos.keys())

        # Time grid
        self.start_dt = self._parse_time(scenario.start_time_utc)
        self.times: List[datetime] = []
        t = self.start_dt
        end = self.start_dt + timedelta(seconds=scenario.duration_s)
        while t <= end:
            self.times.append(t)
            t += timedelta(seconds=scenario.time_step_s)

        # Propagate once
        self.sat_pos_series = propagate_positions(self.sats, self.times)

        # Pre-compute topology snapshots
        self.snapshots: List[TopologySnapshot] = []
        for i, dt in enumerate(self.times):
            sat_pos = {n: self.sat_pos_series[n][i] for n in self.sats}
            snap = compute_snapshot(
                time_s=i * scenario.time_step_s,
                sat_positions=sat_pos,
                ground_positions=self.ground_pos,
                params=scenario.link,
            )
            self.snapshots.append(snap)

        # Routing protocol
        self.protocol: RoutingProtocol = self._make_protocol()

        # Metrics
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

        # Traffic events
        self.traffic_gen = TrafficGenerator(scenario.traffic, scenario.seed)
        self.traffic_events = self.traffic_gen.generate(scenario.duration_s)

        # Simple in-flight packet queue: (delivery_time, node, packet)
        self.in_flight: List[Tuple[float, str, Packet]] = []

    def _parse_time(self, s: str) -> datetime:
        # Accept ISO or a simple epoch-like string
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            # Fallback: treat as days since a fixed epoch for demo
            return datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _make_protocol(self) -> RoutingProtocol:
        proto = self.scenario.protocol.lower()
        if proto == "aodv":
            return AODVProtocol(self.node_names)
        if proto == "olsr":
            return OLSRProtocol(self.node_names)
        if proto == "predictive":
            p = PredictiveProtocol(self.node_names)
            p.load_snapshots(self.snapshots)
            return p
        raise ValueError(f"Unknown protocol: {proto}")

    def run(self) -> RunMetrics:
        """Execute the simulation and return collected metrics."""
        step = self.scenario.time_step_s
        prev_adj: Dict[str, set] = {n: set() for n in self.node_names}
        traffic_idx = 0

        for i, snap in enumerate(self.snapshots):
            t = snap.time_s

            # 1. Topology change events
            for n in self.node_names:
                old = prev_adj.get(n, set())
                new = snap.adj.get(n, set())
                for lost in old - new:
                    self.protocol.on_link_change(t, n, lost, up=False)
                for gained in new - old:
                    self.protocol.on_link_change(t, n, gained, up=True)
            prev_adj = {n: set(snap.adj.get(n, set())) for n in self.node_names}

            # 2. OLSR periodic tick
            if isinstance(self.protocol, OLSRProtocol):
                for n in self.node_names:
                    for ctrl in self.protocol.tick(t, n):
                        # Immediate local delivery of HELLO / TC to neighbors
                        for neigh in snap.adj.get(n, []):
                            self.protocol.on_packet(t, neigh, ctrl)

            # 3. Inject traffic that is due
            while traffic_idx < len(self.traffic_events) and self.traffic_events[traffic_idx].time_s <= t:
                ev = self.traffic_events[traffic_idx]
                traffic_idx += 1
                flow_id = self._flow_id_for(ev.packet)
                if flow_id:
                    self.metrics.flows[flow_id].sent += 1
                # Hand to routing at the source
                nh = self.protocol.on_packet(t, ev.packet.src, ev.packet)
                if nh is None:
                    # Either delivered (src==dst) or dropped / discovery started
                    if ev.packet.src == ev.packet.dst and flow_id:
                        self.metrics.flows[flow_id].delivered += 1
                    elif flow_id:
                        self.metrics.flows[flow_id].dropped += 1
                else:
                    self._send(t, ev.packet.src, nh, ev.packet, snap)

            # 4. Deliver in-flight packets whose arrival time has come
            still_flying = []
            for arr_t, node, pkt in self.in_flight:
                if arr_t <= t:
                    nh = self.protocol.on_packet(t, node, pkt)
                    flow_id = self._flow_id_for(pkt)
                    if nh is None:
                        if node == pkt.dst:
                            if flow_id:
                                fm = self.metrics.flows[flow_id]
                                fm.delivered += 1
                                fm.latencies_s.append(t - pkt.creation_time_s)
                                fm.hop_counts.append(len(pkt.path))
                        else:
                            if flow_id:
                                self.metrics.flows[flow_id].dropped += 1
                    else:
                        self._send(t, node, nh, pkt, snap)
                else:
                    still_flying.append((arr_t, node, pkt))
            self.in_flight = still_flying

            # 5. Track peak state size
            for n in self.node_names:
                sz = self.protocol.state_size(n)
                prev = self.metrics.peak_state_bytes.get(n, 0)
                if sz > prev:
                    self.metrics.peak_state_bytes[n] = sz

        # Final control counters
        self.metrics.control_messages_sent = self.protocol.control_messages_sent()
        self.metrics.control_messages_recv = self.protocol.control_messages_received()
        self.metrics.route_changes = sum(
            1 for e in self.protocol.route_events if e.event_type == "route_change"
        )
        # Approximate discovery times
        starts = { (e.node, e.destination): e.time_s
                   for e in self.protocol.route_events if e.event_type == "discovery_start" }
        for e in self.protocol.route_events:
            if e.event_type == "discovery_end":
                key = (e.node, e.destination)
                if key in starts:
                    self.metrics.discovery_times_s.append(e.time_s - starts[key])

        return self.metrics

    def _send(self, now: float, src: str, nh: str, pkt: Packet, snap: TopologySnapshot) -> None:
        if nh == "__broadcast__":
            for neigh in snap.adj.get(src, []):
                # Find delay
                delay = 0.001
                for e in snap.edges:
                    if (e.u == src and e.v == neigh) or (e.v == src and e.u == neigh):
                        delay = e.delay_s
                        break
                new_pkt = Packet(
                    pkt_id=pkt.pkt_id,
                    src=pkt.src,
                    dst=pkt.dst,
                    type=pkt.type,
                    size_bytes=pkt.size_bytes,
                    creation_time_s=pkt.creation_time_s,
                    ttl=pkt.ttl,
                    payload=pkt.payload,
                    path=pkt.path + [src],
                )
                self.in_flight.append((now + delay, neigh, new_pkt))
            return

        # Unicast
        delay = 0.001
        for e in snap.edges:
            if (e.u == src and e.v == nh) or (e.v == src and e.u == nh):
                delay = e.delay_s
                break
        pkt.path.append(src)
        self.in_flight.append((now + delay, nh, pkt))

    def _flow_id_for(self, pkt: Packet) -> Optional[str]:
        for f in self.scenario.traffic:
            if f.source == pkt.src and f.destination == pkt.dst:
                return f.flow_id
        return None


def run_scenario(scenario_path: str | Path, output_dir: str | Path = "outputs") -> RunMetrics:
    """Convenience entry point."""
    scenario_path = Path(scenario_path).resolve()
    scenario = load_scenario(scenario_path)

    # Resolve relative TLE path against the scenario file location
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
