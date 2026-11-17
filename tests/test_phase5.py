"""Phase 5: connectivity-aware endpoints, stats CIs, fixed-mode compatibility."""

from __future__ import annotations

import numpy as np

from connectivity import (
    connected_components,
    fixed_endpoints,
    select_endpoints_connectivity_aware,
)
from experiments.stats_summary import summarize_values
from links import LinkEdge, TopologySnapshot


def _snap(edges, nodes):
    adj = {n: set() for n in nodes}
    le = []
    for u, v in edges:
        adj[u].add(v)
        adj[v].add(u)
        le.append(LinkEdge(u, v, 0.001, 100.0, 1000.0))
    return TopologySnapshot(0.0, le, adj)


def test_connected_components_two_islands():
    nodes = ["A", "B", "C", "D"]
    snap = _snap([("A", "B"), ("C", "D")], nodes)
    comps = connected_components(snap.adj)
    assert len(comps) == 2
    sizes = sorted(len(c) for c in comps)
    assert sizes == [2, 2]


def test_connectivity_aware_same_component():
    nodes = list("ABCDEF")
    # One component of 4, one of 2
    edges = [("A", "B"), ("B", "C"), ("C", "D"), ("E", "F")]
    snap = _snap(edges, nodes)
    rng = np.random.default_rng(0)
    choice = select_endpoints_connectivity_aware(snap, rng, min_component_size=3)
    assert choice is not None
    assert choice.mode == "connectivity_aware"
    assert choice.source != choice.destination
    assert choice.component_size >= 3
    # Both in {A,B,C,D}
    assert choice.source in "ABCD"
    assert choice.destination in "ABCD"


def test_connectivity_aware_none_if_too_small():
    snap = _snap([("A", "B")], ["A", "B", "C"])
    rng = np.random.default_rng(1)
    assert select_endpoints_connectivity_aware(snap, rng, min_component_size=5) is None


def test_fixed_endpoints_backward_compatible():
    ep = fixed_endpoints("X", "Y")
    assert ep.mode == "fixed"
    assert ep.source == "X" and ep.destination == "Y"


def test_student_t_ci_basic():
    s = summarize_values([1.0, 2.0, 3.0], method="student_t")
    assert s["n"] == 3
    assert s["mean"] == 2.0
    assert s["ci95_lo"] is not None and s["ci95_hi"] is not None
    assert s["ci95_lo"] < s["mean"] < s["ci95_hi"]
    assert s["flag"] is None


def test_zero_variance_flagged():
    s = summarize_values([5.0, 5.0, 5.0], method="student_t")
    assert s["flag"] == "zero_variance_degenerate_ci"
    assert s["ci95_lo"] == 5.0


def test_insufficient_sample():
    s = summarize_values([1.0], method="student_t")
    assert s["flag"] == "insufficient_sample_for_ci"
    assert s["ci95_lo"] is None


def test_empty_samples():
    s = summarize_values([], method="student_t")
    assert s["flag"] == "no_samples"
