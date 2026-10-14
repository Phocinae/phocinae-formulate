"""Recorded predictions.

This is what makes the tool usable with no decision service running: a
`predictions.jsonl` file holds what the model answered, and every metric is
computed from it. The `--scan` path writes exactly this format, so a live run and
a replay are analysed by the same code.
"""

from __future__ import annotations

import json
import os

from .errors import FormulateError

#: Provenance kinds that must never be measured as if they were real.
RECORDED_KINDS = ("recorded",)
#: Kinds allowed only for tests, never for a `--scan` replacement.
SYNTHETIC_KINDS = ("synthetic", "buggy-guard")


class Prediction(object):
    __slots__ = ("id", "formulation", "answer", "confidence", "option_scores", "flagged", "provenance")

    def __init__(self, id, formulation, answer, confidence, option_scores, flagged, provenance):
        self.id = id
        self.formulation = formulation
        self.answer = answer
        self.confidence = confidence
        self.option_scores = option_scores
        self.flagged = flagged
        self.provenance = provenance

    @property
    def has_confidence(self):
        return self.confidence is not None


def load_predictions(path, allow_synthetic=False):
    """Load a predictions file.

    Returns ``(header, predictions)`` where ``header`` carries file-level
    provenance. A file whose provenance is synthetic is refused unless
    ``allow_synthetic`` is set: a hand-written fixture must not be mistaken for a
    measurement.
    """
    if not os.path.isfile(path):
        raise FormulateError("E_PREDICTIONS_NOT_FOUND", "predictions file %r does not exist" % path)

    header = None
    predictions = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise FormulateError("E_PREDICTIONS",
                                     "line %d is not valid JSON: %s" % (number, exc))
            if not isinstance(row, dict):
                raise FormulateError("E_PREDICTIONS", "line %d is not an object" % number)
            if row.get("record") == "header":
                header = row
                continue
            if row.get("record") not in (None, "prediction"):
                raise FormulateError("E_PREDICTIONS",
                                     "line %d: unknown record kind %r" % (number, row.get("record")))

            record_id = row.get("id")
            if not isinstance(record_id, str) or not record_id.strip():
                raise FormulateError("E_PREDICTIONS", "line %d: 'id' must be a non-empty string" % number)
            formulation = row.get("formulation")
            if not isinstance(formulation, str) or not formulation.strip():
                raise FormulateError("E_PREDICTIONS",
                                     "line %d: 'formulation' must be a non-empty string" % number)

            confidence = row.get("confidence")
            if confidence is not None:
                if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                    raise FormulateError("E_PREDICTIONS",
                                         "line %d: 'confidence' must be a number or null" % number)
                confidence = float(confidence)

            flagged = row.get("flagged")
            if flagged is not None and not isinstance(flagged, bool):
                raise FormulateError("E_PREDICTIONS",
                                     "line %d: 'flagged' must be a boolean or null" % number)

            provenance = row.get("provenance")
            if not isinstance(provenance, dict) or not provenance.get("kind"):
                raise FormulateError(
                    "E_PREDICTIONS",
                    "line %d: 'provenance.kind' is required -- an unattributed number "
                    "cannot be told apart from a measurement" % number,
                )
            kind = provenance.get("kind")
            if kind in SYNTHETIC_KINDS and not allow_synthetic:
                raise FormulateError(
                    "E_SYNTHETIC_REFUSED",
                    "line %d: provenance kind %r is refused for analysis; it exists for tests "
                    "only (pass --allow-synthetic to override deliberately)" % (number, kind),
                )

            predictions.append(Prediction(
                id=record_id,
                formulation=formulation,
                answer=row.get("answer"),
                confidence=confidence,
                option_scores=row.get("option_scores"),
                flagged=flagged,
                provenance=provenance,
            ))

    if header is None:
        raise FormulateError("E_PREDICTIONS",
                             "%r has no header record; the file's provenance is unknown" % path)
    if not predictions:
        raise FormulateError("E_EMPTY_SCAN",
                             "%r has a header but no predictions: nothing to measure" % path)
    return header, predictions


def group_by_formulation(predictions):
    grouped = {}
    for prediction in predictions:
        grouped.setdefault(prediction.formulation, []).append(prediction)
    return grouped
