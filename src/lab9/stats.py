"""Statistics students implement (M2 judge calibration, M4 paired bootstrap).

Rules: a judge is a classifier -- measure it like one. A higher mean is not
"better" until the paired CI of the difference excludes 0.
Reference values to self-check are in tests/test_lab9_stats.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import comb

POS = "FAIL"   # the judge's job is to FIND failures, so FAIL is the positive class


def confusion(human: Sequence[str], judge: Sequence[str]) -> dict[str, int]:
    """tp/fp/fn/tn with FAIL as positive."""
    tp = sum(1 for h, j in zip(human, judge) if h == POS and j == POS)
    fp = sum(1 for h, j in zip(human, judge) if h != POS and j == POS)
    fn = sum(1 for h, j in zip(human, judge) if h == POS and j != POS)
    tn = sum(1 for h, j in zip(human, judge) if h != POS and j != POS)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def precision_recall(human: Sequence[str], judge: Sequence[str]) -> tuple[float, float]:
    # M2: precision = tp/(tp+fp), recall = tp/(tp+fn); return 0.0 when the denominator is 0
    cm = confusion(human, judge)
    tp, fp, fn = cm["tp"], cm["fp"], cm["fn"]
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return precision, recall


def cohen_kappa(human: Sequence[str], judge: Sequence[str]) -> float:
    """kappa = (p_o - p_e) / (1 - p_e). Raise ValueError when undefined (p_e == 1):
    a constant judge has no signal and must FAIL calibration, not pass on a technicality."""
    n = len(human)
    if n == 0:
        raise ValueError("kappa undefined for empty input")
    cm = confusion(human, judge)
    tp, fp, fn, tn = cm["tp"], cm["fp"], cm["fn"], cm["tn"]
    p_o = (tp + tn) / n
    # Expected agreement by chance from the marginal distributions.
    h_fail = tp + fn
    h_pass = fp + tn
    j_fail = tp + fp
    j_pass = fn + tn
    p_e = (h_fail * j_fail + h_pass * j_pass) / (n * n)
    denom = 1.0 - p_e
    if denom <= 1e-12:
        raise ValueError("kappa undefined (p_e == 1): a constant judge has no signal")
    return (p_o - p_e) / denom


def rogan_gladen(observed_rate: float, sensitivity: float, specificity: float) -> float:
    """Correct an observed failure rate for judge error; clip to [0, 1]."""
    denom = sensitivity + specificity - 1.0
    if abs(denom) < 1e-12:
        # A judge no better than chance cannot correct anything; return the clipped observation.
        return max(0.0, min(1.0, observed_rate))
    corrected = (observed_rate + specificity - 1.0) / denom
    return max(0.0, min(1.0, corrected))


def paired_bootstrap(a: Sequence[float], b: Sequence[float], *, n_resamples: int = 1000,
                     rng: int = 2027, confidence: float = 0.95) -> tuple[float, float, float]:
    """a[i], b[i] = per-item mean over seeds, SAME items, SAME order.
    Return (mean(a)-mean(b), lo, hi) with scipy.stats.bootstrap(paired=True, method="percentile",
    rng=numpy.random.default_rng(rng)). If a == b everywhere return (0.0, 0.0, 0.0)."""
    import numpy as np
    from scipy import stats as scipy_stats

    a_arr = np.asarray(a, dtype=float)
    b_arr = np.asarray(b, dtype=float)
    if np.array_equal(a_arr, b_arr):
        return (0.0, 0.0, 0.0)

    def diff(x: np.ndarray, y: np.ndarray, axis: int = 0) -> np.ndarray:
        return np.mean(x, axis=axis) - np.mean(y, axis=axis)

    res = scipy_stats.bootstrap(
        (a_arr, b_arr), diff, paired=True, n_resamples=n_resamples, method="percentile",
        rng=np.random.default_rng(rng), confidence_level=confidence, vectorized=True,
    )
    return (float(np.mean(a_arr) - np.mean(b_arr)),
            float(res.confidence_interval.low), float(res.confidence_interval.high))


def _check_k_args(n: int, c: int, k: int) -> None:
    if n < 0 or c < 0 or c > n:
        raise ValueError(f"invalid counts: n={n}, c={c}")
    if k < 1 or k > n:
        raise ValueError(f"invalid k={k} for n={n} trials")


def _c(n: int, k: int) -> int:
    if k < 0 or n < 0 or k > n:
        return 0
    return comb(n, k)


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that AT LEAST ONE of k trials drawn from n (c of them passing) passes.
    Unbiased estimator (Chen et al. 2021): 1 - C(n-c, k) / C(n, k). Raise ValueError if k > n."""
    _check_k_args(n, c, k)
    return 1.0 - _c(n - c, k) / _c(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k (tau-bench, Yao et al. 2024): probability that ALL k trials drawn from n pass.
    Unbiased estimator: C(c, k) / C(n, k). Raise ValueError if k > n.
    pass^k <= pass@1 <= pass@k; a reliable agent keeps pass^k close to pass@1."""
    _check_k_args(n, c, k)
    return _c(c, k) / _c(n, k)
