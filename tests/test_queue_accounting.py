"""Regression tests for packet fate and service-queue accounting."""

from __future__ import annotations

from engine import Simulator
from routing.base import Packet, PacketType


def _data_packet(pkt_id: int) -> Packet:
    return Packet(
        pkt_id=pkt_id,
        src="A",
        dst="B",
        type=PacketType.DATA,
        size_bytes=125_000,
        creation_time_s=0.0,
    )


def _bare_simulator(queue_limit: int) -> Simulator:
    sim = object.__new__(Simulator)
    sim.queue_limit = queue_limit
    sim._link_free_at = {}
    sim._link_finish_times = {}
    return sim


def test_queue_limit_tracks_serialization_until_service_completes():
    sim = _bare_simulator(queue_limit=2)
    first = sim._reserve_link(0.0, "A", "B", _data_packet(1), 1.0)
    second = sim._reserve_link(0.1, "A", "B", _data_packet(2), 1.0)

    assert first == (0.0, 1.0)
    assert second == (1.0, 1.0)
    assert sim._reserve_link(0.2, "A", "B", _data_packet(3), 1.0) is None

    # The first transmission has completed, so the link can accept another packet.
    assert sim._reserve_link(1.01, "A", "B", _data_packet(4), 1.0) is not None
