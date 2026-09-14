"""Paired equivalence analysis as library code.

The generic form of SDA-002's registered analysis, usable by any study whose
unit yields a within-unit difference ``d = f_treatment − f_reference``:

* :func:`tost_verdict` — two one-sided tests via confidence-interval
  inclusion, with the 2×2 verdict (equivalent / trivially different /
  different / inconclusive).
* :func:`bootstrap_ci` — percentile bootstrap interval for the mean d.
* :func:`sample_size` — pairs needed for a paired TOST at a bound δ with a
  residual shift θ (θ = 0 is the optimistic case).
* :func:`spread_stats` — SD ratio and pairing correlation between the
  treatment and reference values across units.
* :func:`paired_gap_test` — one-sided paired Wilcoxon on |d| between two
  conditions (the "calibration helped" test).

All functions take plain sequences and return plain dicts so results can be
written straight into a results file that validates against a schema.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy import stats

VERDICTS = ("equivalent", "trivially_different", "different", "inconclusive")


def _as_array(x: Sequence[float]) -> np.ndarray:
    a = np.asarray(list(x), dtype=float)
    a = a[~np.isnan(a)]
    return a


def tost_verdict(diffs: Sequence[float], delta: float, alpha: float = 0.05) -> dict:
    """TOST at level ``alpha`` via the (1 − 2α) interval, plus a (1 − α) interval for zero.

    Verdicts:
      equivalent           — 90% CI inside (−δ, δ) and 95% CI includes 0
      trivially_different  — 90% CI inside (−δ, δ) and 95% CI excludes 0
      different            — 90% CI not inside and 95% CI excludes 0
      inconclusive         — 90% CI not inside and 95% CI includes 0
    """
    if delta <= 0:
        raise ValueError("delta must be positive")
    d = _as_array(diffs)
    n = int(d.size)
    if n < 2:
        raise ValueError("need at least 2 paired differences")
    mean = float(d.mean())
    sd = float(d.std(ddof=1))
    se = sd / math.sqrt(n)
    df = n - 1
    t_eq = stats.t.ppf(1 - alpha, df)
    t_zero = stats.t.ppf(1 - alpha / 2, df)
    ci_eq = (mean - t_eq * se, mean + t_eq * se)
    ci_zero = (mean - t_zero * se, mean + t_zero * se)
    equivalent = ci_eq[0] > -delta and ci_eq[1] < delta
    different = ci_zero[0] > 0 or ci_zero[1] < 0
    if equivalent and not different:
        verdict = "equivalent"
    elif equivalent and different:
        verdict = "trivially_different"
    elif different:
        verdict = "different"
    else:
        verdict = "inconclusive"
    return {
        "n": n,
        "mean_d": mean,
        "sd_d": sd,
        "se": se,
        "delta": float(delta),
        "alpha": alpha,
        "ci_equivalence": [float(ci_eq[0]), float(ci_eq[1])],
        "ci_zero": [float(ci_zero[0]), float(ci_zero[1])],
        "equivalent": bool(equivalent),
        "different": bool(different),
        "verdict": verdict,
        "d_over_delta": abs(mean) / delta,
    }


def bootstrap_ci(
    diffs: Sequence[float], level: float = 0.90, reps: int = 5000, seed: int = 0
) -> list[float]:
    """Percentile bootstrap interval for the mean of ``diffs``."""
    d = _as_array(diffs)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, d.size, size=(reps, d.size))
    means = d[idx].mean(axis=1)
    lo, hi = np.percentile(means, [100 * (1 - level) / 2, 100 * (1 + level) / 2])
    return [float(lo), float(hi)]


def sample_size(
    sd_d: float, delta: float, theta: float = 0.0, power: float = 0.9, alpha: float = 0.05
) -> int:
    """Pairs needed for a paired TOST (normal approximation).

    With θ = 0 both one-sided tests matter and the two-sided power split is
    used; with θ ≠ 0 the nearer bound dominates and the margin is δ − |θ|.
    Returns None-like ``0`` when |θ| ≥ δ (equivalence cannot be shown).
    """
    if delta <= 0 or sd_d <= 0:
        raise ValueError("delta and sd_d must be positive")
    if abs(theta) >= delta:
        return 0
    z_a = stats.norm.ppf(1 - alpha)
    if theta == 0:
        z_b = stats.norm.ppf(1 - (1 - power) / 2)
        n = ((z_a + z_b) * sd_d / delta) ** 2
    else:
        z_b = stats.norm.ppf(power)
        n = ((z_a + z_b) * sd_d / (delta - abs(theta))) ** 2
    return int(math.ceil(n))


def power_at(n: int, sd_d: float, delta: float, theta: float = 0.0, alpha: float = 0.05) -> float:
    """Power of a paired TOST at n pairs (normal approximation)."""
    if n < 2:
        return 0.0
    se = sd_d / math.sqrt(n)
    z_a = stats.norm.ppf(1 - alpha)
    p = stats.norm.cdf((delta - theta) / se - z_a) + stats.norm.cdf((delta + theta) / se - z_a) - 1
    return float(max(0.0, p))


def spread_stats(f_treatment: Sequence[float], f_reference: Sequence[float]) -> dict:
    """SD ratio and pairing correlation across units. Both inputs aligned by unit."""
    a = np.asarray(list(f_treatment), dtype=float)
    b = np.asarray(list(f_reference), dtype=float)
    mask = ~(np.isnan(a) | np.isnan(b))
    a, b = a[mask], b[mask]
    if a.size < 3:
        return {"n": int(a.size), "sd_ratio": None, "r": None}
    sd_ref = float(b.std(ddof=1))
    ratio = float(a.std(ddof=1) / sd_ref) if sd_ref > 0 else None
    r = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else None
    return {"n": int(a.size), "sd_ratio": ratio, "r": r}


def paired_gap_test(d_treated: Sequence[float], d_control: Sequence[float]) -> dict:
    """One-sided paired Wilcoxon: is |d| smaller under treatment than control?

    Inputs are aligned by unit. Returns the p-value for the alternative
    ``|d_treated| < |d_control|`` and the median paired reduction.
    """
    a = np.abs(np.asarray(list(d_treated), dtype=float))
    b = np.abs(np.asarray(list(d_control), dtype=float))
    mask = ~(np.isnan(a) | np.isnan(b))
    a, b = a[mask], b[mask]
    if a.size < 5:
        return {"n": int(a.size), "p_value": None, "median_reduction": None}
    res = stats.wilcoxon(b, a, alternative="greater", zero_method="wilcox")
    return {
        "n": int(a.size),
        "p_value": float(res.pvalue),
        "median_reduction": float(np.median(b - a)),
    }


def holm(p_values: Sequence[float | None]) -> list[float | None]:
    """Holm step-down adjusted p-values; None entries pass through."""
    idx = [i for i, p in enumerate(p_values) if p is not None]
    m = len(idx)
    out: list[float | None] = [None] * len(p_values)
    order = sorted(idx, key=lambda i: p_values[i])
    running = 0.0
    for rank, i in enumerate(order):
        adj = min(1.0, (m - rank) * p_values[i])
        running = max(running, adj)
        out[i] = running
    return out
