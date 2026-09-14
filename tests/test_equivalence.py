"""Tests for studies.equivalence — the paired-equivalence library."""

import numpy as np
import pytest

from studies import equivalence as eq


def test_verdicts_cover_the_2x2():
    rng = np.random.default_rng(0)
    # equivalent: tight around zero, bound wide
    d = rng.normal(0.0, 0.1, 200)
    assert eq.tost_verdict(d, delta=0.1)["verdict"] == "equivalent"
    # trivially different: clearly nonzero but inside the bound
    d = rng.normal(0.03, 0.05, 400)
    assert eq.tost_verdict(d, delta=0.1)["verdict"] == "trivially_different"
    # different: far outside
    d = rng.normal(0.5, 0.1, 100)
    assert eq.tost_verdict(d, delta=0.1)["verdict"] == "different"
    # inconclusive: noisy, few
    d = rng.normal(0.0, 1.0, 8)
    assert eq.tost_verdict(d, delta=0.1)["verdict"] == "inconclusive"


def test_verdict_shape_and_validation():
    r = eq.tost_verdict([0.1, -0.1, 0.05, -0.05], delta=1.0)
    assert set(r) >= {"n", "mean_d", "sd_d", "ci_equivalence", "ci_zero", "verdict", "d_over_delta"}
    with pytest.raises(ValueError):
        eq.tost_verdict([0.1], delta=1.0)
    with pytest.raises(ValueError):
        eq.tost_verdict([0.1, 0.2], delta=0)


def test_sample_size_matches_known_values():
    # SDA-002 power-table-v13 (theta = 0, 90%): utterance_length sd_d 0.3454, delta 0.093035 -> 150
    assert eq.sample_size(0.3454, 0.093035, theta=0.0, power=0.9) == 150
    assert eq.sample_size(0.0312, 0.007859, theta=0.0, power=0.9) == 171
    # a residual shift raises n; a shift at the bound makes equivalence unreachable
    assert eq.sample_size(0.3454, 0.093035, theta=0.045, power=0.9) > 150
    assert eq.sample_size(0.3454, 0.093035, theta=0.1, power=0.9) == 0


def test_power_increases_with_n():
    p50 = eq.power_at(50, 0.3454, 0.093035)
    p150 = eq.power_at(150, 0.3454, 0.093035)
    assert 0 <= p50 < p150 <= 1
    assert abs(p150 - 0.90) < 0.02


def test_bootstrap_ci_brackets_mean():
    rng = np.random.default_rng(1)
    d = rng.normal(0.2, 1.0, 300)
    lo, hi = eq.bootstrap_ci(d, level=0.9, reps=2000, seed=3)
    assert lo < d.mean() < hi


def test_spread_stats_detects_variance_collapse():
    rng = np.random.default_rng(2)
    real = rng.normal(0, 1, 100)
    gen = 0.1 * rng.normal(0, 1, 100)  # same mean, a tenth of the spread, no pairing
    s = eq.spread_stats(gen, real)
    assert s["sd_ratio"] < 0.2 and abs(s["r"]) < 0.3
    s2 = eq.spread_stats(real + rng.normal(0, 0.1, 100), real)
    assert s2["sd_ratio"] > 0.9 and s2["r"] > 0.9


def test_paired_gap_test_and_holm():
    rng = np.random.default_rng(3)
    control = rng.normal(0.5, 0.2, 60)
    treated = control * 0.3 + rng.normal(0, 0.02, 60)
    r = eq.paired_gap_test(treated, control)
    assert r["p_value"] < 0.001 and r["median_reduction"] > 0
    assert eq.holm([0.01, None, 0.04, 0.03]) == [0.03, None, 0.06, 0.06]
