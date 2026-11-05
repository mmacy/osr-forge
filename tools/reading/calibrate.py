"""The calibration: a small logistic model per style that turns Clef's answer and the ink into P(passable).

The model is logistic regression on standardised features, fitted by iteratively reweighted least squares with a
small ridge, in numpy alone. Its features are named sets:

- `clef`: Clef's passable probability and its door probability (door, locked door, and secret door) as log-odds,
  clipped to plus or minus 8
- `ink`: the ink measurements of [`EdgeInk`][tools.reading.ink.EdgeInk]
- `clef+ink`: both

The screening scores each map with a calibration fitted without it (`held_out`): leave one map out where the
style has several maps, else spatial folds of 12 by 12 windows.

[`ranked_share`][tools.reading.calibrate.ranked_share] is the share of answers, ranked from most to least
confident, that come before the first wrong one, which the plan judges Clef by.
"""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

__all__ = [
    "Logistic",
    "decision_errors",
    "expected_calibration_error",
    "fold_of",
    "held_out",
    "ranked_share",
]

_CLIP = 8.0


def logit(p: np.ndarray) -> np.ndarray:
    """Log-odds, clipped to plus or minus 8."""
    q = np.clip(p, 1e-6, 1 - 1e-6)
    return np.clip(np.log(q / (1 - q)), -_CLIP, _CLIP)


@dataclass
class Logistic:
    """Logistic regression with a ridge on the weights (not the intercept)."""

    ridge: float = 1.0
    mean: np.ndarray | None = None
    scale: np.ndarray | None = None
    weights: np.ndarray | None = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> Logistic:
        """Fit on features `x` (n by k) and outcomes `y` (n, 0 or 1)."""
        self.mean = x.mean(axis=0)
        self.scale = np.where(x.std(axis=0) > 1e-9, x.std(axis=0), 1.0)
        z = np.hstack([np.ones((len(x), 1)), (x - self.mean) / self.scale])
        w = np.zeros(z.shape[1])
        penalty = np.full(z.shape[1], self.ridge)
        penalty[0] = 0.0
        for _ in range(50):
            p = 1 / (1 + np.exp(-(z @ w)))
            weight = np.clip(p * (1 - p), 1e-9, None)
            gradient = z.T @ (p - y) + penalty * w
            hessian = (z * weight[:, None]).T @ z + np.diag(penalty)
            step = np.linalg.solve(hessian, gradient)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        self.weights = w
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        """P(outcome) for features `x`."""
        assert self.mean is not None and self.scale is not None and self.weights is not None
        z = np.hstack([np.ones((len(x), 1)), (x - self.mean) / self.scale])
        return 1 / (1 + np.exp(-(z @ self.weights)))


def fold_of(map_id: str, cell: tuple[int, int], maps: Sequence[str], folds: int = 5) -> str:
    """The fold an edge belongs to: its map when the style has several maps, else a 12 by 12 window's fold."""
    if len(set(maps)) > 1:
        return map_id
    x, y = cell
    return f"{map_id}#{(x // 12 * 7 + y // 12 * 3) % folds}"


def held_out(x: np.ndarray, y: np.ndarray, groups: Sequence[str], ridge: float = 1.0) -> np.ndarray:
    """Each row's probability from a model fitted on the other groups.

    Args:
        x: Features, n by k.
        y: Outcomes, 0 or 1.
        groups: Each row's group.
        ridge: The ridge.

    Returns:
        The held-out probabilities.
    """
    groups_array = np.array(groups)
    out = np.zeros(len(y))
    for group in sorted(set(groups)):
        test = groups_array == group
        train = ~test
        if train.sum() == 0 or len(set(y[train].tolist())) < 2:
            out[test] = y[train].mean() if train.any() else 0.5
            continue
        out[test] = Logistic(ridge=ridge).fit(x[train], y[train]).predict(x[test])
    return out


def ranked_share(y: np.ndarray, p: np.ndarray, threshold: float, nth: int = 1) -> tuple[int, int]:
    """How many decisions, ranked from most to least confident, come before the `nth` wrong one.

    Args:
        y: Whether each edge is passable in the key.
        p: P(passable).
        threshold: The passable threshold that makes each decision.
        nth: Which wrong decision ends the count: the first, as the plan judges Clef, or a later one, which one
            confident error (or one error of the key's) can't decide alone.

    Returns:
        `(decisions before the nth wrong one, decisions)`.
    """
    decision = p >= threshold
    confidence = np.where(decision, p, 1 - p)
    order = np.argsort(-confidence, kind="stable")
    wrong = np.flatnonzero(decision[order] != y[order].astype(bool))
    return (int(wrong[nth - 1]) if len(wrong) >= nth else len(y)), len(y)


def decision_errors(y: np.ndarray, p: np.ndarray, threshold: float) -> tuple[int, int]:
    """`(walls read as open, open read as blocked)` at a threshold."""
    decision = p >= threshold
    passable = y.astype(bool)
    return int((decision & ~passable).sum()), int((~decision & passable).sum())


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """The mean gap between confidence and accuracy over equal-width bins of the decision's confidence."""
    decision = p >= 0.5
    confidence = np.where(decision, p, 1 - p)
    right = decision == y.astype(bool)
    edges = np.linspace(0.5, 1.0, bins + 1)
    total = 0.0
    for low, high in itertools.pairwise(edges):
        inside = (confidence >= low) & ((confidence < high) | (high == 1.0))
        if inside.any():
            total += inside.sum() * abs(confidence[inside].mean() - right[inside].mean())
    return total / len(y) if len(y) else 0.0
