"""Calibration: expected calibration error and the confidence distribution.

ECE is defined here explicitly because there is more than one reasonable
definition and an unstated one makes correct numbers look wrong:

    fixed-width bins over [0, 1] (``bins`` equal-width buckets, last bucket
    closed), each contributing ``|accuracy - mean_confidence|`` **weighted by the
    number of samples in that bucket**.

A record whose confidence is missing contributes nothing and is counted in
``missing``. It is never treated as confidence 0 -- that would drag every bucket
down and manufacture a calibration problem that is really a data gap.
"""

from __future__ import annotations

from .metrics import percentile_summary


def ece(confidences, correct, bins=10):
    """Expected calibration error. ``None`` when no record has a confidence."""
    if bins < 1:
        raise ValueError("bins must be >= 1")
    buckets = [[] for _ in range(bins)]
    for confidence, is_correct in zip(confidences, correct):
        if confidence is None:
            continue
        value = min(1.0, max(0.0, float(confidence)))
        index = int(value * bins)
        if index >= bins:
            index = bins - 1
        buckets[index].append((value, bool(is_correct)))

    total = sum(len(bucket) for bucket in buckets)
    if total == 0:
        return None

    error = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        mean_confidence = sum(value for value, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, ok in bucket if ok) / len(bucket)
        error += (len(bucket) / float(total)) * abs(accuracy - mean_confidence)
    return error


def calibration_summary(confidences, correct, bins=10):
    present = [c for c in confidences if c is not None]
    return {
        "ece": round(ece(confidences, correct, bins), 6) if present else None,
        "bins": bins,
        "confidence_available": bool(present),
        "confidence_missing": sum(1 for c in confidences if c is None),
        "confidence_percentiles": percentile_summary(present) if present else None,
    }


def bucket_table(confidences, correct, bins=10):
    """Per-bucket detail, so a high ECE can be traced to a specific band."""
    buckets = [[] for _ in range(bins)]
    for confidence, is_correct in zip(confidences, correct):
        if confidence is None:
            continue
        value = min(1.0, max(0.0, float(confidence)))
        index = min(bins - 1, int(value * bins))
        buckets[index].append((value, bool(is_correct)))
    rows = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        mean_confidence = sum(value for value, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, ok in bucket if ok) / len(bucket)
        rows.append({
            "bucket": index,
            "low": round(index / float(bins), 6),
            "high": round((index + 1) / float(bins), 6),
            "n": len(bucket),
            "mean_confidence": round(mean_confidence, 6),
            "accuracy": round(accuracy, 6),
            "gap": round(accuracy - mean_confidence, 6),
        })
    return rows
