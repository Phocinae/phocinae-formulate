"""The labelled set.

One line is one decidable unit -- not "one task with several questions" -- so that
every formulation can be asked about exactly the same objects.
"""

from __future__ import annotations

import json
import os

from .errors import FormulateError, EXIT_EMPTY_SCAN


class Record(object):
    __slots__ = ("id", "state", "risk", "safety_gold", "task_gold", "scenario", "notes")

    def __init__(self, id, state, risk=None, safety_gold=None, task_gold=None,
                 scenario=None, notes=None):
        self.id = id
        self.state = state
        self.risk = risk
        self.safety_gold = safety_gold
        self.task_gold = task_gold
        self.scenario = scenario
        self.notes = notes

    @property
    def decidable(self):
        return self.safety_gold is not None or self.risk is not None or self.task_gold is not None

    @property
    def positive(self):
        """Is this record one the formulation should flag?

        ``safety_gold: true`` means "this should have been stopped". A task-level
        ``review`` or ``deny`` also counts: neither is a pass, and the gate is
        fail-closed.
        """
        if self.safety_gold is not None:
            return bool(self.safety_gold)
        if self.task_gold is not None:
            return self.task_gold in ("review", "deny")
        return None


def _require(condition, message, reason="E_GOLD"):
    if not condition:
        raise FormulateError(reason, message)


def _parse_line(line, number, seen):
    try:
        row = json.loads(line)
    except ValueError as exc:
        raise FormulateError("E_GOLD", "line %d is not valid JSON: %s" % (number, exc))
    _require(isinstance(row, dict), "line %d is not an object" % number)

    record_id = row.get("id")
    _require(isinstance(record_id, str) and record_id.strip(),
             "line %d: 'id' must be a non-empty string" % number)
    _require(record_id not in seen, "line %d: duplicate id %r" % (number, record_id))
    seen.add(record_id)

    state = row.get("state")
    _require(isinstance(state, str) and state.strip(),
             "line %d: 'state' must be a non-empty string" % number)

    gold = row.get("gold")
    _require(isinstance(gold, dict), "line %d: 'gold' must be an object" % number)

    risk = gold.get("risk")
    if risk is not None:
        _require(isinstance(risk, int) and not isinstance(risk, bool),
                 "line %d: gold.risk must be an integer" % number)

    safety = gold.get("safety_gold")
    if safety is not None:
        _require(isinstance(safety, bool), "line %d: gold.safety_gold must be a boolean" % number)

    task = gold.get("task_gold")
    if task is not None:
        _require(task in ("allow", "review", "deny"),
                 "line %d: gold.task_gold must be allow / review / deny, got %r" % (number, task))

    return Record(id=record_id, state=state, risk=risk, safety_gold=safety,
                  task_gold=task, scenario=row.get("scenario"), notes=row.get("notes"))


def load_gold(path):
    """Load a gold set. Raises on schema problems, duplicate ids and empty state."""
    if not os.path.isfile(path):
        raise FormulateError("E_GOLD_NOT_FOUND", "gold file %r does not exist" % path)
    records = []
    seen = set()
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            records.append(_parse_line(line, number, seen))
    if not records:
        # Exit 4, not the default 3: an empty gold file is a legitimate but
        # unmeasurable dataset, not a malformed one. Leaving one reason code on
        # two different exit codes would make the contract unguessable.
        raise FormulateError("E_EMPTY_SCAN",
                             "gold file %r has no records: a check with nothing to check "
                             "must not report success" % path,
                             EXIT_EMPTY_SCAN)
    return records


def check_gold_consistency(records):
    """Do ``risk`` and ``safety_gold`` agree?

    If both are present they must be separable by some threshold: every
    ``safety_gold: true`` record must be strictly riskier than every
    ``safety_gold: false`` record. If they overlap, no cut can reproduce the
    labels and any sweep over them would be meaningless.
    """
    with_risk = [r for r in records if r.risk is not None and r.safety_gold is not None]
    if not with_risk:
        return []
    positives = [r.risk for r in with_risk if r.safety_gold]
    negatives = [r.risk for r in with_risk if not r.safety_gold]
    if not positives or not negatives:
        return []
    if min(positives) <= max(negatives):
        return [FormulateError(
            "GOLD_INCONSISTENT",
            "gold.risk and gold.safety_gold disagree: the riskiest routine record is %d "
            "but the safest destructive record is %d, so no cut separates them"
            % (max(negatives), min(positives)),
        )]
    return []
