"""Confusion-matrix metrics.

One distinction matters more than any other here, and it is the difference
between two results that both look like "zero recall":

    recall == 0.0    the formulation flagged nothing, and there *were* positives
    recall is None   there were no positives, so the question is unanswerable

Reporting the second as 0.0 would blame a formulation for a property of the
dataset. Every metric that can be undefined returns ``None`` rather than a
comfortable zero, and the report renders it as ``null``.
"""

from __future__ import annotations

from .errors import FormulateError


class Confusion(object):
    __slots__ = ("tp", "fn", "fp", "tn")

    def __init__(self, tp, fn, fp, tn):
        self.tp = tp
        self.fn = fn
        self.fp = fp
        self.tn = tn

    @property
    def n(self):
        return self.tp + self.fn + self.fp + self.tn

    @property
    def positives(self):
        return self.tp + self.fn

    @property
    def negatives(self):
        return self.fp + self.tn

    @property
    def recall(self):
        """Share of positives that were flagged. ``None`` when there are none."""
        if self.positives == 0:
            return None
        return float(self.tp) / self.positives

    @property
    def missed_allow(self):
        """Share of positives that were let through. ``None`` when there are none."""
        if self.positives == 0:
            return None
        return float(self.fn) / self.positives

    @property
    def model_false_block(self):
        """Share of routine records the formulation flagged. ``None`` when there are none."""
        if self.negatives == 0:
            return None
        return float(self.fp) / self.negatives

    @property
    def specificity(self):
        if self.negatives == 0:
            return None
        return float(self.tn) / self.negatives

    @property
    def accuracy(self):
        if self.n == 0:
            return None
        return float(self.tp + self.tn) / self.n

    @property
    def precision(self):
        flagged = self.tp + self.fp
        if flagged == 0:
            return None
        return float(self.tp) / flagged

    def as_dict(self):
        return {
            "tp": self.tp, "fn": self.fn, "fp": self.fp, "tn": self.tn,
            "n": self.n, "positives": self.positives, "negatives": self.negatives,
        }


def confusion(pairs):
    """Build a :class:`Confusion` from ``(flagged, positive)`` pairs.

    A pair whose flag is ``None`` cannot be scored -- the formulation produced an
    answer that does not decide anything (a score with no cut, say). Those are
    excluded and counted separately by the caller, never silently treated as
    "not flagged", which would inflate recall.
    """
    tp = fn = fp = tn = 0
    for flagged, positive in pairs:
        if flagged is None or positive is None:
            continue
        if positive and flagged:
            tp += 1
        elif positive and not flagged:
            fn += 1
        elif not positive and flagged:
            fp += 1
        else:
            tn += 1
    return Confusion(tp, fn, fp, tn)


def undecided(pairs):
    """How many pairs could not be scored."""
    return sum(1 for flagged, positive in pairs if flagged is None or positive is None)


def percentile(values, fraction):
    """Nearest-rank percentile: ``ceil(fraction * (n + 1))``, 1-based.

    Nearest-rank rather than interpolation because it always returns a value that
    is actually in the data, which matters when the report is quoted as "the
    model's confidence at the 75th percentile". The convention is stated here
    because two reasonable conventions give different numbers, and an unstated
    convention makes a correct result look wrong.
    """
    if not values:
        raise FormulateError("E_EMPTY", "percentile of an empty sequence is undefined")
    ordered = sorted(values)
    n = len(ordered)
    if fraction <= 0:
        return ordered[0]
    if fraction >= 1:
        return ordered[-1]
    import math

    rank = int(math.ceil(fraction * (n + 1)))
    if rank < 1:
        rank = 1
    if rank > n:
        rank = n
    return ordered[rank - 1]


def percentile_summary(values):
    """min / p25 / p50 / p75 / max, or ``None`` when there are no values."""
    if not values:
        return None
    return {
        "min": round(min(values), 6),
        "p25": round(percentile(values, 0.25), 6),
        "p50": round(percentile(values, 0.50), 6),
        "p75": round(percentile(values, 0.75), 6),
        "max": round(max(values), 6),
        "n": len(values),
    }
