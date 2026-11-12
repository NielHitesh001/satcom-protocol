"""Traffic generation and cross-process reproducibility tests."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from config import TrafficFlow
from traffic import TrafficGenerator


def _poisson_flow() -> TrafficFlow:
    return TrafficFlow(
        flow_id="f",
        source="A",
        destination="B",
        start_s=0.0,
        duration_s=20.0,
        rate_pps=2.0,
        arrival_model="poisson",
    )


def test_poisson_workload_is_seeded_and_fingerprinted():
    first = TrafficGenerator([_poisson_flow()], seed=11).generate(20.0)
    second = TrafficGenerator([_poisson_flow()], seed=11).generate(20.0)
    third = TrafficGenerator([_poisson_flow()], seed=12).generate(20.0)

    assert TrafficGenerator.fingerprint(first) == TrafficGenerator.fingerprint(second)
    assert TrafficGenerator.fingerprint(first) != TrafficGenerator.fingerprint(third)


def test_route_tie_is_reproducible_across_hash_seeds():
    root = Path(__file__).resolve().parent.parent
    program = """
from links import LinkEdge, TopologySnapshot
from routing.predictive import PredictiveProtocol
edges = [LinkEdge('A', 'B', .1, 1., 1.), LinkEdge('A', 'C', .1, 1., 1.),
         LinkEdge('B', 'D', .1, 1., 1.), LinkEdge('C', 'D', .1, 1., 1.)]
adj = {'A': {'B', 'C'}, 'B': {'A', 'D'}, 'C': {'A', 'D'}, 'D': {'B', 'C'}}
p = PredictiveProtocol(['A', 'B', 'C', 'D'])
p.load_snapshots([TopologySnapshot(0., edges, adj)])
print(p.next_hop('A', 'D', 0.))
"""
    outputs = []
    for hash_seed in ("1", "2", "999"):
        env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONPATH=str(root))
        outputs.append(subprocess.check_output(
            [sys.executable, "-c", program], cwd=root, env=env, text=True
        ).strip())
    assert outputs == ["B", "B", "B"]
