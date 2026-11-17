"""
Configurable control-message size assumptions.

These integers are **model assumptions**, not captures from flight hardware.
A single PDU size is used for serialization delay:

    serialization_s = size_bytes * 8 / (capacity_mbps * 1e6)

Layers included: one modeled control PDU.
Layers NOT modeled separately: network header, link header, PHY overhead.
Do not treat the totals as complete wire-level byte accounting.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict


@dataclass
class ControlSizeModel:
    aodv_rreq: int = 64
    aodv_rrep: int = 64
    aodv_rerr_base: int = 48
    aodv_rerr_per_dest: int = 8
    olsr_hello: int = 48
    olsr_tc: int = 64
    # Practical predictive does not emit packets. This size is an estimate
    # applied per node on schedule refresh and is NOT airtime.
    practical_refresh_per_node: int = 32

    def rerr_size(self, n_unreachable: int) -> int:
        return int(self.aodv_rerr_base + self.aodv_rerr_per_dest * max(0, n_unreachable))

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["layers_included"] = "single_modeled_control_pdu"
        d["layers_excluded"] = "network_header,link_header,phy"
        d["provenance"] = "configurable_assumption_not_hardware_measurement"
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any] | None) -> "ControlSizeModel":
        if not raw:
            return cls()
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: int(v) for k, v in raw.items() if k in allowed}
        return cls(**kwargs)


def serialization_s(size_bytes: int, capacity_mbps: float) -> float:
    if capacity_mbps <= 0:
        return 0.0
    return (int(size_bytes) * 8.0) / (capacity_mbps * 1e6)
