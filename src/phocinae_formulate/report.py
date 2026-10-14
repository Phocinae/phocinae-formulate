"""Assemble the report.

Byte-stability is a requirement rather than a nicety: the report is consumed by
tests and by CI, and a report that reorders itself or embeds a timestamp cannot
be compared. The plain report has no clock in it; ``stamped`` output is a
separate, clearly-named artefact.
"""

from __future__ import annotations

import json

from .errors import FormulateError
from .calibration import calibration_summary, bucket_table
from .formulations import FORMULATIONS, FORMULATION_ORDER
from .metrics import confusion, undecided
from .predictions import RECORDED_KINDS
from .taucan import tau_sweep
from .verdict import decide_formulation, overall, rank

SCHEMA = "phocinae-formulate/report/v1"


def _round(value, digits=6):
    return None if value is None else round(float(value), digits)


def analyse(gold_records, predictions, formulations=None, tau_grid=None,
            bins=10, min_recall=None, max_false_block=None, min_positives=None):
    """Run the analysis. Returns the decision payload (no report wrapper).

    When ``formulations`` is not given, every formulation present in the
    predictions is analysed: the four built-ins in their canonical order first,
    then anything else in sorted order. Measured data must never be dropped
    because a name was not on a list -- a silent skip is indistinguishable from
    "we measured it and it was fine".
    """
    by_id = {record.id: record for record in gold_records}
    grouped = {}
    for prediction in predictions:
        grouped.setdefault(prediction.formulation, []).append(prediction)

    if formulations:
        names = list(formulations)
    else:
        known = [name for name in FORMULATION_ORDER if name in grouped]
        extra = sorted(name for name in grouped if name not in FORMULATION_ORDER)
        names = known + extra
    if not names:
        raise FormulateError(
            "E_EMPTY_SCAN",
            "no formulation appears in the predictions: there is nothing to measure",
        )

    per_formulation = {}
    decisions = []

    for name in names:
        # A name discovered from the data may not be one of the built-ins. That is
        # not an error: the flagship fixture carries derived variants
        # (bool-threshold-0.5, bool-negated) that are re-analyses of a recorded
        # run. Only the flag rule needs to be known, and the recording already
        # carries the resulting `flagged` value, so an unknown name can still be
        # measured -- its question shape is simply reported as unknown.
        formulation = FORMULATIONS.get(name)
        rows = grouped.get(name)
        if rows is None:
            per_formulation[name] = {
                "status": "SKIP",
                "skip_reason": "no recorded predictions for this formulation",
            }
            continue

        pairs = []
        confidences = []
        correctness = []
        tau_rows = []
        unknown_ids = []
        for prediction in rows:
            record = by_id.get(prediction.id)
            if record is None:
                unknown_ids.append(prediction.id)
                continue
            positive = record.positive
            flagged = prediction.flagged
            pairs.append((flagged, positive))
            if prediction.has_confidence:
                confidences.append(prediction.confidence)
                correctness.append(None if flagged is None or positive is None
                                   else flagged == positive)
                tau_rows.append((prediction.confidence, flagged, positive))

        if not pairs:
            per_formulation[name] = {
                "status": "SKIP",
                "skip_reason": "no prediction matched a gold record",
            }
            continue

        matrix = confusion(pairs)
        skipped = undecided(pairs)
        calibration = calibration_summary(confidences, correctness, bins)
        tau_result = tau_sweep(tau_rows, grid=tau_grid)

        kwargs = {}
        if min_recall is not None:
            kwargs["min_recall"] = min_recall
        if max_false_block is not None:
            kwargs["max_false_block"] = max_false_block
        if min_positives is not None:
            kwargs["min_positives"] = min_positives
        decision = decide_formulation(name, matrix, tau_result, **kwargs)
        decisions.append(decision)

        per_formulation[name] = {
            "status": "OK",
            "question_type": formulation.question_type if formulation else None,
            "ask": formulation.ask if formulation else None,
            "options_k": formulation.options_k if formulation else None,
            "threshold_sent": formulation.threshold if formulation else None,
            "known_formulation": formulation is not None,
            "cross_k_comparable": False,
            "cited_from": formulation.cited_from if formulation else None,
            "provenance_kinds": sorted({p.provenance.get("kind") for p in rows}),
            "counts": matrix.as_dict(),
            "undecided_rows": skipped,
            "unmatched_ids": sorted(set(unknown_ids)),
            "recall": decision.recall,
            "missed_allow": _round(matrix.missed_allow),
            "model_false_block": decision.false_block,
            "specificity": _round(matrix.specificity),
            "accuracy": _round(matrix.accuracy),
            "precision": _round(matrix.precision),
            # Ratios that a human reads are rounded; the two headline rates
            # (recall, false-block) keep full precision because they are the
            # values the fixtures assert exactly.
            "recall_display": _fmt(decision.recall),
            "model_false_block_display": _fmt(decision.false_block),
            "calibration": calibration,
            "calibration_buckets": bucket_table(confidences, correctness, bins),
            "tau": {
                "selected": tau_result["tau"],
                "candidates": tau_result["tau_candidates"],
                "degenerate_points": tau_result["degenerate_points"],
                "missing_confidence": tau_result["missing_confidence"],
                "reason": tau_result["reason"],
                "rows": tau_result["rows"],
            },
            "decision": decision.as_dict(),
        }

    top = overall(decisions) if decisions else {
        "do": "don't_ship", "formulation": None, "tau": None,
        "any_dont_ship": False, "dont_ship_formulations": [], "headline": [],
    }
    return {
        "formulations": per_formulation,
        "decision": top,
        "ranked": [d.name for d in rank(decisions)],
    }


def build_report(payload, gold_path, prediction_sources, counts, service_state,
                 expectations=None, schema=SCHEMA):
    """Wrap a decision payload in the report envelope.

    ``service_state`` records whether a live service was contacted. A report must
    never imply a live run when it replayed a recording, so this field is
    required rather than optional.
    """
    report = {
        "schema": schema,
        "gold": gold_path,
        "sources": list(prediction_sources),
        "counts": counts,
        "service": service_state,
        "formulations": payload["formulations"],
        "decision": payload["decision"],
        "ranked": payload["ranked"],
    }
    if expectations is not None:
        report["expectations"] = expectations
    return report


def dumps(payload):
    """Canonical JSON text: sorted keys, UTF-8, no timestamp, trailing newline."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False) + "\n"


def render_text(report):
    """Human-readable rendering. The JSON above is the machine contract."""
    lines = []
    lines.append("phocinae-formulate report")
    lines.append("  gold: %s" % report.get("gold"))
    lines.append("  sources: %s" % ", ".join(report.get("sources") or []))
    service = report.get("service") or {}
    lines.append("  service: %s" % service.get("state", "unknown"))
    lines.append("")
    for name in sorted(report.get("formulations") or {}):
        entry = report["formulations"][name]
        if entry.get("status") != "OK":
            lines.append("%-14s SKIP  (%s)" % (name, entry.get("skip_reason")))
            continue
        counts = entry["counts"]
        lines.append("%-14s %s   recall=%s  false-block=%s  tp=%d/fn=%d/fp=%d/tn=%d  tau=%s"
                     % (name, entry["decision"]["do"].upper(),
                        _fmt(entry["recall"]), _fmt(entry["model_false_block"]),
                        counts["tp"], counts["fn"], counts["fp"], counts["tn"],
                        entry["tau"]["selected"]))
        for reason in entry["decision"]["fail_reasons"]:
            lines.append("                 - %s" % reason)
    lines.append("")
    lines.append("SUMMARY " + " ".join(
        "%s=%s" % (k, v) for k, v in sorted((report.get("counts") or {}).items())))
    for line in report["decision"]["headline"]:
        lines.append(line)
    lines.append("")
    lines.append("overall: %s%s" % (
        report["decision"]["do"],
        " (%s)" % report["decision"]["formulation"] if report["decision"]["formulation"] else ""))
    if report["decision"]["any_dont_ship"]:
        lines.append("do not use: %s" % ", ".join(report["decision"]["dont_ship_formulations"]))
    return "\n".join(lines) + "\n"


def _fmt(value):
    return "null" if value is None else ("%.6f" % value)


def _render_text_uses(value):
    return "null" if value is None else ("%.4f" % value)
