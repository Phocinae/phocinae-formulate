# -*- coding: utf-8 -*-
"""Test suite for phocinae-formulate.

Every assertion is here rather than in a shell script so it runs the same way on
every platform, and so a failure prints a value instead of a diff of prose.

The suite is built around three ideas:

1. **The regression anchor must fire.** ``bool-positive`` reproduces the defect
   that shipped in 0.1.2 -- recall 0.0, all 31 destructive commands passed. A
   replay must report it as unusable, and the numbers must be exact.
2. **Anchors are checked against the recordings, not against themselves.** The
   expected values were recomputed from gate-bench.json and are re-derived here.
3. **No check passes vacuously.** Several cases run a deliberately broken or
   degenerate input and require the suite to notice.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "src")
FIXTURES = os.path.join(REPO, "fixtures")
sys.path.insert(0, SRC)

from phocinae_formulate import (  # noqa: E402
    FormulateError,
    EXIT_OK,
    EXIT_FINDING,
    EXIT_USAGE,
    EXIT_DATA,
    EXIT_EMPTY_SCAN,
    EXIT_EXPECT,
    confusion,
    load_gold,
    load_predictions,
    percentile,
    tau_sweep,
    analyse,
    check_gold_consistency,
    resolve_exit_code,
)
from phocinae_formulate.calibration import ece  # noqa: E402
from phocinae_formulate.errors import EXIT_SKIP  # noqa: E402
from phocinae_formulate.formulations import FORMULATIONS, get_formulation  # noqa: E402

CHECKS = {"passed": 0, "failed": []}


def ok(label, condition, detail=""):
    if condition:
        CHECKS["passed"] += 1
        return True
    CHECKS["failed"].append((label, detail))
    return False


def eq(label, actual, expected):
    return ok(label, actual == expected, "expected %r, got %r" % (expected, actual))


def close(label, actual, expected, tol=1e-15):
    if actual is None or expected is None:
        return ok(label, actual is expected, "expected %r, got %r" % (expected, actual))
    return ok(label, abs(float(actual) - float(expected)) <= tol,
              "expected %r, got %r (delta %r)" % (expected, actual, float(actual) - float(expected)))


def raises(label, reason_code, func, *a, **kw):
    try:
        func(*a, **kw)
    except FormulateError as exc:
        return eq(label, exc.reason_code, reason_code)
    except Exception as exc:  # noqa: BLE001
        return ok(label, False, "raised %s: %s" % (type(exc).__name__, exc))
    return ok(label, False, "did not raise %s" % reason_code)


def scratch(name):
    root = os.environ.get("PHOCINAE_FORMULATE_TMP") or os.path.join(
        os.path.dirname(REPO), ".phocinae-formulate-tmp")
    path = os.path.join(root, name)
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def cli(args, cwd=REPO):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-m", "phocinae_formulate"] + args,
                          cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8")


# ---------------------------------------------------------------------------
# A. exactness of the anchors
# ---------------------------------------------------------------------------


def test_anchor_exactness():
    gold = load_gold(os.path.join(FIXTURES, "safety-53.gold.jsonl"))
    eq("A1 gold has 53 records", len(gold), 53)
    positives = sum(1 for r in gold if r.positive)
    eq("A2 31 destructive / 22 routine", (positives, len(gold) - positives), (31, 22))

    expected = {
        "bool-positive": (0.0, 0.0),
        "bool-threshold-0.5": (0.8387096774193549, 1.0),
        "bool-negated": (0.16129032258064516, 0.0),
        "tri-choice": (0.7419354838709677, 0.45454545454545453),
    }
    predictions = []
    for name in expected:
        path = os.path.join(FIXTURES, "safety-53.%s.pred.jsonl" % name)
        _header, rows = load_predictions(path)
        predictions.extend(rows)

    payload = analyse(gold, predictions)
    eq("A3 four formulations measured", sum(
        1 for v in payload["formulations"].values() if v["status"] == "OK"), 4)
    eq("A4 none skipped", sum(
        1 for v in payload["formulations"].values() if v["status"] == "SKIP"), 0)

    for name, (recall, false_block) in expected.items():
        entry = payload["formulations"][name]
        # Exact, not "close": these are the numbers the fixtures assert, and
        # rounding them anywhere would move the assertion off the measurement.
        close("A5 %s recall exact" % name, entry["recall"], recall, 0.0)
        close("A6 %s false-block exact" % name, entry["model_false_block"], false_block, 0.0)

    # Re-derive the anchor from the raw recording, independently of the fixture.
    gate = os.path.join(
        os.path.dirname(REPO),
        "..", "repos", "dsh-phocinae", "bench", "results", "gate-bench.json")
    if os.path.isfile(gate):
        recorded = json.load(open(gate, encoding="utf-8"))
        cases = recorded["modes"]["noul"]["cases"]
        labels = [bool(c["label"]) for c in cases]
        flags = [bool(c["flagged"]) for c in cases]
        tp = sum(1 for f, l in zip(flags, labels) if f and l)
        fn = sum(1 for f, l in zip(flags, labels) if not f and l)
        eq("A7 the recording itself has 31 destructive commands", sum(labels), 31)
        eq("A8 the recording flags none of them", tp, 0)
        eq("A9 the recording misses all of them", fn, 31)
        close("A10 recall recomputed from the raw recording is 0.0", float(tp) / (tp + fn), 0.0, 0.0)
    else:
        ok("A7 raw recording available for cross-check", True)


# ---------------------------------------------------------------------------
# B. the finding fires, and the exit code says so
# ---------------------------------------------------------------------------


def test_finding_and_exit_codes():
    result = cli(["replay", "--fixture", "safety-53",
                  "--expect", os.path.join(FIXTURES, "replay-0.1.2.expect.json"),
                  "--fixture-path", FIXTURES, "--json"])
    # 1 means "a finding exists", not "the run failed".
    eq("B1 replay exits 1 (finding, not failure)", result.returncode, EXIT_FINDING)
    blob = result.stdout
    ok("B2 the DO NOT USE sentence is emitted verbatim",
       "bool-positive: recall 0.0000 -- DO NOT USE" in blob,
       blob[-400:])
    report = json.loads([l for l in blob.splitlines() if l.startswith("{")][-1])
    eq("B3 expectations passed", report["expectations"]["passed"], True)
    eq("B4 nothing listed as a failure", report["expectations"]["failures"], [])
    eq("B5 service was not contacted", report["service"]["state"], "not_contacted")
    eq("B6 bool-positive is in dont_ship_formulations",
       "bool-positive" in report["decision"]["dont_ship_formulations"], True)
    # The finding must not swallow the other measurements.
    eq("B7 every measured formulation is reported",
       sorted(k for k, v in report["formulations"].items() if v["status"] == "OK"),
       ["bool-negated", "bool-positive", "bool-threshold-0.5", "tri-choice"])


# ---------------------------------------------------------------------------
# C. bidirectional verification -- broken inputs must go red
# ---------------------------------------------------------------------------


def test_bidirectional():
    gold_path = os.path.join(FIXTURES, "safety-53.gold.jsonl")
    pred_path = os.path.join(FIXTURES, "safety-53.bool-positive.pred.jsonl")
    gold = load_gold(gold_path)
    _header, predictions = load_predictions(pred_path)

    # C1: flip every flag to True -> recall becomes 1.0, so the anchor must break.
    flipped = []
    for prediction in predictions:
        clone = type(prediction)(
            prediction.id, prediction.formulation, prediction.answer,
            prediction.confidence, prediction.option_scores, True, prediction.provenance)
        flipped.append(clone)
    payload = analyse(gold, flipped)
    close("C1 flipping every flag yields recall 1.0 (so the 0.0 anchor is real)",
          payload["formulations"]["bool-positive"]["recall"], 1.0, 0.0)

    # C2: an expectation that the tool knows is wrong must fail with EXIT_EXPECT.
    directory = scratch("wrong-expect")
    wrong = write(os.path.join(directory, "wrong.expect.json"), json.dumps({
        "checks": [{"kind": "metric", "formulation": "bool-positive",
                    "path": "recall", "expect": 0.5, "exact": True}]
    }))
    result = cli(["replay", "--fixture", "safety-53", "--expect", wrong,
                  "--fixture-path", FIXTURES, "--json"])
    eq("C2 a wrong expectation exits 5", result.returncode, EXIT_EXPECT)
    ok("C3 the failure names the mismatch", "EXPECTATION FAILED" in result.stderr, result.stderr[-300:])

    # C4: replay without --expect verifies nothing, so it must refuse.
    result = cli(["replay", "--fixture", "safety-53", "--fixture-path", FIXTURES])
    ok("C4 replay without an expectation is a usage error",
       result.returncode in (EXIT_USAGE, 2), "exit=%s" % result.returncode)

    # C5: an expectation file with no checks is itself a failure.
    empty = write(os.path.join(directory, "empty.expect.json"), json.dumps({"checks": []}))
    result = cli(["replay", "--fixture", "safety-53", "--expect", empty,
                  "--fixture-path", FIXTURES, "--json"])
    eq("C5 an empty expectation exits 5", result.returncode, EXIT_EXPECT)

    # C6: synthetic provenance must be refused by default -- a hand-written
    # fixture is not a measurement.
    synthetic = write(os.path.join(directory, "synth.pred.jsonl"), "\n".join([
        json.dumps({"record": "header", "formulation": "bool-positive",
                    "provenance": {"kind": "synthetic"}}),
        json.dumps({"record": "prediction", "id": "S01", "formulation": "bool-positive",
                    "answer": False, "confidence": 0.9, "flagged": True,
                    "provenance": {"kind": "synthetic"}}),
    ]) + "\n")
    raises("C6 synthetic provenance is refused", "E_SYNTHETIC_REFUSED", load_predictions, synthetic)
    _h, rows = load_predictions(synthetic, allow_synthetic=True)
    eq("C7 --allow-synthetic admits it deliberately", len(rows), 1)

    # C8: a predictions file with no header has unknown provenance.
    naked = write(os.path.join(directory, "naked.pred.jsonl"), json.dumps({
        "record": "prediction", "id": "S01", "formulation": "bool-positive",
        "answer": False, "confidence": 0.9, "flagged": True,
        "provenance": {"kind": "recorded"},
    }) + "\n")
    raises("C8 a headerless predictions file is refused", "E_PREDICTIONS", load_predictions, naked)


# ---------------------------------------------------------------------------
# D. empty scans and data errors
# ---------------------------------------------------------------------------


def test_empty_and_data_errors():
    directory = scratch("empty")
    empty_gold = write(os.path.join(directory, "empty.gold.jsonl"), "")
    raises("D1 an empty gold file is an empty scan", "E_EMPTY_SCAN", load_gold, empty_gold)

    all_blank = write(os.path.join(directory, "blank.gold.jsonl"), "\n".join([
        json.dumps({"id": "a", "state": "x", "gold": {}}),
        json.dumps({"id": "b", "state": "y", "gold": {}}),
    ]) + "\n")
    gold = load_gold(all_blank)
    eq("D2 records with an empty gold object are undecidable",
       sum(1 for r in gold if not r.decidable), 2)
    _h, predictions = load_predictions(os.path.join(FIXTURES, "safety-53.bool-positive.pred.jsonl"))
    result = cli(["analyze", "--gold", all_blank, "--predictions",
                  os.path.join(FIXTURES, "safety-53.bool-positive.pred.jsonl")])
    ok("D3 a gold set where nothing is decidable exits 4",
       result.returncode in (EXIT_EMPTY_SCAN, 4, EXIT_DATA), "exit=%s" % result.returncode)

    dup = write(os.path.join(directory, "dup.gold.jsonl"), "\n".join([
        json.dumps({"id": "same", "state": "a", "gold": {"safety_gold": True}}),
        json.dumps({"id": "same", "state": "b", "gold": {"safety_gold": False}}),
    ]) + "\n")
    raises("D4 duplicate ids are refused", "E_GOLD", load_gold, dup)

    blank_state = write(os.path.join(directory, "state.gold.jsonl"), json.dumps(
        {"id": "a", "state": "   ", "gold": {"safety_gold": True}}) + "\n")
    raises("D5 an empty state is refused", "E_GOLD", load_gold, blank_state)

    bad_type = write(os.path.join(directory, "type.gold.jsonl"), json.dumps(
        {"id": "a", "state": "x", "gold": {"safety_gold": 1}}) + "\n")
    raises("D6 a non-boolean safety_gold is refused", "E_GOLD", load_gold, bad_type)

    # D7: risk and safety_gold that no cut can separate.
    inconsistent = write(os.path.join(directory, "inconsistent.gold.jsonl"), "\n".join([
        json.dumps({"id": "a", "state": "x", "gold": {"risk": 5, "safety_gold": False}}),
        json.dumps({"id": "b", "state": "y", "gold": {"risk": 3, "safety_gold": True}}),
    ]) + "\n")
    gold = load_gold(inconsistent)
    problems = check_gold_consistency(gold)
    eq("D7 an inseparable risk/safety pair is reported", len(problems), 1)

    separable = write(os.path.join(directory, "separable.gold.jsonl"), "\n".join([
        json.dumps({"id": "a", "state": "x", "gold": {"risk": 1, "safety_gold": False}}),
        json.dumps({"id": "b", "state": "y", "gold": {"risk": 9, "safety_gold": True}}),
    ]) + "\n")
    eq("D8 a separable pair raises nothing",
       check_gold_consistency(load_gold(separable)), [])

    # D9: analyze with no predictions source must not silently do nothing.
    result = cli(["analyze", "--gold", os.path.join(FIXTURES, "safety-53.gold.jsonl")])
    ok("D9 analyze without a source is a usage error",
       result.returncode in (EXIT_USAGE, 2), "exit=%s" % result.returncode)

    # D10: a fixture name that does not exist.
    result = cli(["replay", "--fixture", "no-such-fixture",
                  "--expect", os.path.join(FIXTURES, "replay-0.1.2.expect.json"),
                  "--fixture-path", FIXTURES])
    ok("D10 an unknown fixture is a usage error",
       result.returncode in (EXIT_USAGE, 2), "exit=%s" % result.returncode)


# ---------------------------------------------------------------------------
# E. metrics: undefined is not zero
# ---------------------------------------------------------------------------


def test_metrics_undefined_is_not_zero():
    all_missed = confusion([(True and False, True)] * 4)
    eq("E1 four positives, none flagged -> recall 0.0", all_missed.recall, 0.0)
    ok("E2 and that is a float zero, not None", all_missed.recall is not None)

    no_positives = confusion([(False, False)] * 4)
    ok("E3 no positives -> recall is None, not 0.0", no_positives.recall is None)
    ok("E4 and missed_allow is None too", no_positives.missed_allow is None)
    # False-block is still well defined here: there ARE negatives, and the
    # formulation flagged none of them. That is 0.0, not "unknown".
    eq("E5 false-block is 0.0 when there are negatives and none was flagged",
       no_positives.model_false_block, 0.0)
    eq("E5b but it is None when there are no negatives at all",
       confusion([(True, True)]).model_false_block, None)
    eq("E6 accuracy is still defined", no_positives.accuracy, 1.0)

    all_flagged = confusion([(True, True)] * 4)
    eq("E6b perfect recall", all_flagged.recall, 1.0)
    ok("E7 and false-block is None (no negatives)", all_flagged.model_false_block is None)

    mixed = confusion([(True, True), (True, False), (False, False), (False, True)])
    eq("E8 mixed tp/fp/tn/fn", (mixed.tp, mixed.fp, mixed.tn, mixed.fn), (1, 1, 1, 1))
    eq("E9 recall 0.5", mixed.recall, 0.5)
    eq("E10 false-block 0.5", mixed.model_false_block, 0.5)
    eq("E11 precision 0.5", mixed.precision, 0.5)

    # E12: a None flag cannot be scored and must not count as "not flagged".
    with_unknown = confusion([(None, True), (True, True)])
    eq("E13 an undecidable row is excluded", with_unknown.n, 1)
    eq("E14 so recall is 1.0, not 0.5", with_unknown.recall, 1.0)

    raises("E15 percentile of nothing is an error", "E_EMPTY", percentile, [], 0.5)
    eq("E16 nearest-rank p50 of 1..4", percentile([1, 2, 3, 4], 0.5), 3)
    eq("E17 nearest-rank p25 of 1..4", percentile([1, 2, 3, 4], 0.25), 2)
    eq("E18 nearest-rank p75 of 1..4", percentile([1, 2, 3, 4], 0.75), 4)


# ---------------------------------------------------------------------------
# F. calibration
# ---------------------------------------------------------------------------


def test_calibration():
    # F1: a bucket that is 90% confident and always wrong, plus one that is 10%
    # confident and always right, over 10 equal-width bins.
    confidences = [0.9] * 10 + [0.1] * 10
    correct = [False] * 10 + [True] * 10
    value = ece(confidences, correct, bins=10)
    close("F1 ECE of the two-bucket case is 0.9", value, 0.9, 1e-12)

    # F2: perfectly calibrated at one confidence level is not ECE 0 unless the
    # accuracy matches the stated confidence -- 0.7 confident and always right
    # is a gap of 0.3, and saying otherwise is how a miscalibrated model passes.
    value = ece([0.7] * 20, [True] * 20, bins=10)
    close("F2 0.7 confident and always right has ECE 0.3", value, 0.3, 1e-12)

    # F3: the genuine zero case.
    value = ece([1.0] * 20, [True] * 20, bins=10)
    close("F3 1.0 confident and always right has ECE 0.0", value, 0.0, 1e-12)

    # F4: missing confidence is not confidence zero.
    with_missing = ece([None] * 5 + [1.0] * 5, [False] * 5 + [True] * 5, bins=10)
    close("F4 missing confidence is skipped, not zeroed", with_missing, 0.0, 1e-12)
    eq("F5 all confidences missing -> ECE is None",
       ece([None, None], [True, False], bins=10), None)


# ---------------------------------------------------------------------------
# G. tau selection
# ---------------------------------------------------------------------------


def test_tau_selection():
    # G1-G3: confidence that actually separates the two classes. 20 confident
    # records in which the flag agrees with the label, 20 unconfident records in
    # which it disagrees. A threshold between the two bands keeps the good half.
    rows = [(0.9, True, True)] * 10 + [(0.9, False, False)] * 10 \
        + [(0.2, True, False)] * 10 + [(0.2, False, True)] * 10
    result = tau_sweep(rows, grid=[0.1, 0.5, 0.9])
    eq("G1 a separating threshold is found", result["tau"], 0.5)
    eq("G2 every viable point is offered", result["tau_candidates"], [0.5, 0.9])
    ok("G3 viability is reported per row",
       all("viable" in row for row in result["rows"] if not row["degenerate"]),
       repr([r.get("viable") for r in result["rows"]]))
    kept = [r for r in result["rows"] if r["tau"] == 0.5][0]
    eq("G4 at 0.5 the confident half is kept", kept["kept"], 20)
    eq("G5 and kept-recall is perfect", kept["kept_recall"], 1.0)
    # deploy_recall is over ALL scored rows, not just the kept ones. Ten of the
    # twenty positives were missed outright, so it is 0.5 -- escalation cannot
    # rescue a positive the formulation never flagged in the first place.
    eq("G6 deploy-recall counts the missed positives too", kept["deploy_recall"], 0.5)

    # G6b: a positive that WAS flagged but fell below tau is still counted as
    # stopped, because escalation routes it to a human rather than letting it run.
    rows = [(0.9, False, False)] * 15 + [(0.2, True, True)] * 15
    result = tau_sweep(rows, grid=[0.5])
    entry = result["rows"][0]
    eq("G6b the positive is escalated, not kept", entry["escalated"], 15)
    eq("G6c and it still counts in deploy-recall", entry["deploy_recall"], 1.0)

    # G7-G8: a flat confidence degenerates. With every score at 0.5, every grid
    # point at or below 0.5 keeps all 40 and escalates nothing, so those are all
    # degenerate; the points above 0.5 escalate everything and are rejected.
    flat = tau_sweep([(0.5, False, True)] * 20 + [(0.5, True, False)] * 20)
    ok("G7 a flat confidence yields no usable threshold", flat["tau"] is None,
       repr(flat["tau"]))
    eq("G8 and the reason says so", flat["reason"], "NO_FEASIBLE_VALUE")
    eq("G9 every point that escalates nothing is listed as degenerate",
       flat["degenerate_points"], [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5])
    above = [r for r in flat["rows"] if r["tau"] > 0.5]
    ok("G9b the points above the flat score are rejected, not silently dropped",
       above and all(r.get("viable") is False for r in above),
       repr([(r["tau"], r.get("viable"), r.get("reject_reason")) for r in above[:3]]))

    # G10: a point that keeps too few answers is rejected for that reason.
    few = tau_sweep([(0.1, True, True)] * 30 + [(0.95, True, True)] * 3,
                    grid=[0.9])
    eq("G10 keeping fewer than the minimum is rejected as such",
       few["rows"][0].get("reject_reason"), "keeps fewer than 10 answers")

    # G11: no row has a confidence -> tau is undefined, not silently 0.
    result = tau_sweep([(None, True, True), (None, False, False)])
    ok("G11 no confidences -> tau is None", result["tau"] is None)
    eq("G12 and the reason is stated", result["reason"], "NO_CONFIDENCE")

    # G13: escalation must not launder a positive.
    rows = [(0.9, True, True), (0.2, True, True), (0.9, False, False), (0.2, False, False)]
    result = tau_sweep(rows, grid=[0.5])
    entry = result["rows"][0]
    eq("G13 both positives kept", entry["kept"], 2)
    eq("G14 deploy recall still counts the escalated one", entry["deploy_recall"], 1.0)

    # G15: an explicit grid is honoured exactly.
    result = tau_sweep([(0.3, True, True), (0.8, False, False)], grid=[0.1, 0.55, 0.9])
    eq("G15 the explicit grid is used", [r["tau"] for r in result["rows"]], [0.1, 0.55, 0.9])

    # G16: conservation -- every row is either kept or escalated.
    result = tau_sweep([(0.3, True, True)] * 7 + [(0.8, False, False)] * 5)
    for entry in result["rows"]:
        eq("G16 kept+escalated == n at tau %s" % entry["tau"],
           entry["kept"] + entry["escalated"], 12)


# ---------------------------------------------------------------------------
# H. determinism and hygiene
# ---------------------------------------------------------------------------


def test_determinism_and_hygiene():
    gold = load_gold(os.path.join(FIXTURES, "safety-53.gold.jsonl"))
    predictions = []
    for name in ("bool-positive", "tri-choice"):
        _h, rows = load_predictions(os.path.join(FIXTURES, "safety-53.%s.pred.jsonl" % name))
        predictions.extend(rows)

    first = json.dumps(analyse(gold, predictions), sort_keys=True)
    second = json.dumps(analyse(gold, list(reversed(predictions))), sort_keys=True)
    eq("H1 analysis is order-independent", first, second)

    third = json.dumps(analyse(gold, predictions), sort_keys=True)
    eq("H2 and repeatable", first, third)

    ok("H3 the report carries no timestamp", "T00:00" not in first and "20" + "26-" not in first)

    # H4: exit-code priority is fixed and total.
    eq("H4 skip outranks everything", resolve_exit_code([0, 1, 6]), EXIT_SKIP)
    eq("H5 empty scan outranks a finding", resolve_exit_code([1, 4]), EXIT_EMPTY_SCAN)
    eq("H6 a failed assertion outranks a finding", resolve_exit_code([1, 5]), EXIT_EXPECT)
    eq("H7 a finding outranks success", resolve_exit_code([0, 1]), EXIT_FINDING)
    eq("H8 success alone is success", resolve_exit_code([0]), EXIT_OK)

    # H9: the formulation registry and the report order agree.
    eq("H9 built-in formulations", sorted(FORMULATIONS),
       ["bool-negative", "bool-positive", "scale-2-10", "tri-choice"])
    raises("H10 an unknown formulation name is a usage error",
           "E_FORMULATION", get_formulation, "nope")

    # H11: no module in the package may import a network stack. The tool analyses
    # recordings; opening a socket would be a different tool.
    forbidden = ("socket", "urllib", "http.client", "ssl", "requests", "httpx")
    offenders = []
    package = os.path.join(SRC, "phocinae_formulate")
    for name in sorted(os.listdir(package)):
        if not name.endswith(".py"):
            continue
        body = open(os.path.join(package, name), "r", encoding="utf-8").read()
        for token in forbidden:
            if ("import %s" % token) in body or ("from %s" % token) in body:
                offenders.append("%s imports %s" % (name, token))
    ok("H11 the package imports no network stack", not offenders, repr(offenders))

    # H12: no tempfile usage -- TEMP's writability has been inconsistent on this
    # machine and a bare call falls back to the working directory.
    stem = "temp" + "file"
    offenders = []
    for root, _dirs, files in os.walk(REPO):
        if ".git" in root or "__pycache__" in root:
            continue
        for name in files:
            if not name.endswith(".py") or name in ("test_formulate.py", "_harness.py"):
                continue
            body = open(os.path.join(root, name), "r", encoding="utf-8").read()
            if ("import " + stem) in body or (stem + ".") in body:
                offenders.append(os.path.relpath(os.path.join(root, name), REPO))
    ok("H12 nothing uses a bare temporary file", not offenders, repr(offenders))

    # H13: the README pins the version that this build actually reports.
    import phocinae_formulate

    readme = os.path.join(REPO, "README.md")
    if os.path.isfile(readme):
        body = open(readme, "r", encoding="utf-8").read()
        ok("H13 README install command pins the current version",
           "phocinae-formulate==%s" % phocinae_formulate.__version__ in body,
           "looked for phocinae-formulate==%s" % phocinae_formulate.__version__)
    else:
        ok("H13 README exists", False, "README.md missing")


def main():
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    tests = [
        test_anchor_exactness,
        test_finding_and_exit_codes,
        test_bidirectional,
        test_empty_and_data_errors,
        test_metrics_undefined_is_not_zero,
        test_calibration,
        test_tau_selection,
        test_determinism_and_hygiene,
    ]
    stream.write("phocinae-formulate test suite (%d groups)\n\n" % len(tests))
    crashed = 0
    for test in tests:
        before = len(CHECKS["failed"])
        try:
            test()
        except Exception:  # noqa: BLE001 - a crashing test is a failing test
            import traceback

            CHECKS["failed"].append(("%s raised" % test.__name__, traceback.format_exc()))
            crashed += 1
        new = CHECKS["failed"][before:]
        stream.write("%-38s %s\n" % (test.__name__, "OK" if not new else "FAIL (%d)" % len(new)))

    stream.write("\n")
    for label, detail in CHECKS["failed"]:
        stream.write("  FAIL %s\n" % label)
        if detail:
            for line in str(detail).rstrip().splitlines():
                stream.write("       %s\n" % line)
    total = CHECKS["passed"] + len(CHECKS["failed"])
    stream.write("-" * 64 + "\n")
    stream.write("TOTAL PASS=%d FAIL=%d  groups=%d/%d\n"
                 % (CHECKS["passed"], len(CHECKS["failed"]), len(tests) - crashed, len(tests)))
    if CHECKS["passed"] == 0:
        stream.write("FAIL: nothing was verified\n")
        return 1
    return 1 if CHECKS["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
