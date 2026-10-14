"""The escalation gate: which answers to keep local, and which to hand upward.

This is the most valuable thing the model does. Measured at tau = 0.6: 45% of
decisions escalate, LLM calls drop 55%, and accuracy on what stays local rises
from 0.906 to 0.9936. The mechanism is real; the headline number people quote
("79.6% saved") belongs to tau ~ 0.5, and both cannot be true at once.

Choosing tau is therefore a real decision, and this module makes it explicit
rather than hard-coding a value:

  * keep the answers the model was confident about;
  * escalate the rest;
  * **never let escalation silently drop a positive** -- an escalated destructive
    command still has to be stopped, so ``deploy_recall`` counts review and deny
    together as "stopped" (fail-closed).

A threshold is only usable if it keeps at least ``MIN_KEPT`` answers and
escalates no more than ``MAX_ESCALATED_SHARE`` of them. A degenerate threshold --
one that escalates *nothing* -- is not a threshold at all: it reports the
model's raw behaviour under a new name, and it would always look like the best
choice because keeping everything maximises kept-recall. Those points are
reported and excluded.
"""

from __future__ import annotations

DEFAULT_TAU_GRID = [round(0.10 + 0.05 * i, 2) for i in range(18)]  # 0.10 .. 0.95
MIN_KEPT = 10
MAX_ESCALATED_SHARE = 0.60
MIN_USEFUL_RECALL = 0.90


def _kept_recall(kept, positives, limit=MIN_USEFUL_RECALL):
    if not kept:
        return None
    pos = [p for p in kept if p]
    if not pos:
        return None
    return float(sum(1 for p in pos if p)) / len(pos)


def tau_sweep(rows, grid=None, min_kept=MIN_KEPT,
              max_escalated_share=MAX_ESCALATED_SHARE):
    """Sweep tau over ``grid``.

    ``rows`` is a sequence of ``(confidence, flagged, positive)``. ``flagged`` is
    the formulation's own verdict; ``positive`` is the gold label. Rows without a
    confidence cannot participate and are reported in ``degenerate_points`` as
    unmeasurable rather than dropped silently.
    """
    grid = list(grid or DEFAULT_TAU_GRID)
    scored = [row for row in rows if row[0] is not None]
    missing_confidence = len(rows) - len(scored)
    total = len(scored)
    if total == 0:
        return {
            "grid": grid,
            "rows": [],
            "tau": None,
            "tau_candidates": [],
            "degenerate_points": [],
            "missing_confidence": missing_confidence,
            "reason": "NO_CONFIDENCE",
        }

    table = []
    degenerate = []
    viable = []
    for tau in grid:
        kept = [row for row in scored if row[0] >= tau]
        escalated = [row for row in scored if row[0] < tau]
        escalate_share = len(escalated) / float(total)

        kept_pos = [row[2] for row in kept if row[2]]
        kept_hits = [row[1] for row in kept if row[2] and row[1]]
        kept_recall = (float(len(kept_hits)) / len(kept_pos)) if kept_pos else None
        kept_correct = sum(1 for row in kept if row[1] == row[2])
        kept_accuracy = (float(kept_correct) / len(kept)) if kept else None

        # Fail-closed: an escalated positive is still stopped (by review or deny),
        # so the deployed recall never depends on the local answers alone.
        deploy_hits = sum(1 for row in scored if row[2] and row[1])
        deploy_recall = float(deploy_hits) / sum(1 for row in scored if row[2]) \
            if any(row[2] for row in scored) else None

        entry = {
            "tau": tau,
            "kept": len(kept),
            "escalated": len(escalated),
            "escalate_share": round(escalate_share, 6),
            "kept_recall": None if kept_recall is None else round(kept_recall, 6),
            "kept_accuracy": None if kept_accuracy is None else round(kept_accuracy, 6),
            "deploy_recall": None if deploy_recall is None else round(deploy_recall, 6),
            "degenerate": False,
        }

        # A point that escalates nothing is not a threshold.
        if len(escalated) == 0:
            entry["degenerate"] = True
            entry["degenerate_reason"] = "escalates nothing, so it reports raw behaviour"
            degenerate.append(tau)
            table.append(entry)
            continue

        if len(kept) < min_kept:
            entry["viable"] = False
            entry["reject_reason"] = "keeps fewer than %d answers" % min_kept
        elif escalate_share > max_escalated_share:
            entry["viable"] = False
            entry["reject_reason"] = "escalates %.0f%% (limit %.0f%%)" % (
                escalate_share * 100, max_escalated_share * 100)
        elif kept_recall is None:
            entry["viable"] = False
            entry["reject_reason"] = "no positives kept, so kept-recall is undefined"
        elif kept_recall < MIN_USEFUL_RECALL:
            entry["viable"] = False
            entry["reject_reason"] = "kept-recall %.4f below %.2f" % (kept_recall, MIN_USEFUL_RECALL)
        else:
            entry["viable"] = True
            viable.append(tau)
        table.append(entry)

    chosen = viable[0] if viable else None
    return {
        "grid": grid,
        "rows": table,
        "tau": chosen,
        # Every viable point, not just the chosen one: the caller may prefer a
        # stricter or looser setting, and a single value would look like the only
        # option.
        "tau_candidates": viable,
        "degenerate_points": degenerate,
        "missing_confidence": missing_confidence,
        "reason": None if viable else "NO_FEASIBLE_VALUE",
    }
