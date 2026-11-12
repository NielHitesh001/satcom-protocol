"""
Unit tests for metrics aggregation paths.

Percentile convention: linear interpolation on sorted samples,
index k = (n-1) * p/100.
"""

from __future__ import annotations

from metrics import FlowMetrics, RunMetrics, _percentile


def test_percentile_empty_is_none():
    assert _percentile([], 50) is None


def test_percentile_single():
    assert _percentile([4.0], 50) == 4.0
    assert _percentile([4.0], 95) == 4.0


def test_percentile_median_even():
    m = _percentile([1.0, 2.0, 3.0, 4.0], 50)
    assert m is not None
    assert abs(m - 2.5) < 1e-12


def test_percentile_p95():
    xs = [float(i) for i in range(1, 101)]
    p = _percentile(xs, 95)
    assert p is not None
    assert 94.0 <= p <= 96.0


def test_empty_flow_latency_is_none():
    f = FlowMetrics(flow_id="x", src="a", dst="b")
    assert f.mean_latency is None
    assert f.median_latency is None
    assert f.p95_latency is None
    assert f.p99_latency is None
    assert f.jitter is None
    assert f.mean_hops is None


def test_empty_flow_ratios_zero():
    f = FlowMetrics(flow_id="x", src="a", dst="b")
    assert f.delivery_ratio == 0.0
    assert f.loss_ratio == 0.0


def test_residual_not_counted_as_drop():
    f = FlowMetrics(flow_id="x", src="a", dst="b", sent=10, delivered=5, dropped=2, residual=3)
    assert f.sent == f.delivered + f.dropped + f.residual
    assert f.loss_ratio == 0.2
    assert f.delivery_ratio == 0.5


def test_summary_null_latency_when_no_deliveries():
    rm = RunMetrics(
        scenario_name="t",
        protocol="aodv",
        seed=1,
        tle_snapshot_id="x",
        duration_s=10.0,
    )
    rm.flows["f1"] = FlowMetrics(flow_id="f1", src="a", dst="b", sent=5, dropped=5)
    s = rm.summary_dict()
    assert s["mean_latency_s"] is None
    assert s["median_latency_s"] is None
    assert s["p95_latency_s"] is None
    assert s["packets_residual"] == 0
    assert s["loss_ratio"] == 1.0


def test_summary_with_latencies():
    rm = RunMetrics(
        scenario_name="t",
        protocol="predictive",
        seed=1,
        tle_snapshot_id="x",
        duration_s=10.0,
    )
    fm = FlowMetrics(flow_id="f1", src="a", dst="b", sent=3, delivered=3)
    fm.latencies_s = [0.1, 0.2, 0.3]
    fm.hop_counts = [1, 2, 1]
    rm.flows["f1"] = fm
    s = rm.summary_dict()
    assert s["mean_latency_s"] is not None
    assert abs(s["mean_latency_s"] - 0.2) < 1e-12
    assert s["mean_hops"] is not None
    assert abs(s["mean_hops"] - 4 / 3) < 1e-12


def test_manifest_json_handles_none(tmp_path):
    rm = RunMetrics(
        scenario_name="t",
        protocol="aodv",
        seed=1,
        tle_snapshot_id="x",
        duration_s=1.0,
    )
    rm.flows["f"] = FlowMetrics(flow_id="f", src="a", dst="b", sent=1, dropped=1)
    path = tmp_path / "m.json"
    rm.write_manifest(path)
    text = path.read_text()
    assert "null" in text or "mean_latency" in text
