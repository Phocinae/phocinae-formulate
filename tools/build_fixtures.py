# -*- coding: utf-8 -*-
"""Build formulation fixtures from one recorded run, deriving only what is exact.

Everything comes from `gate-bench.json` (a single run: 53 labelled commands, one
state rendering). Using one run matters, because a formulation benchmark asks
"same object, different question" -- mixing runs would confound the question with
the rendering.

Two formulations are **re-analyses** of that run rather than fresh measurements,
and that is recorded as `derivation` in every file:

  tri-choice          the tri-state mode as recorded.
  bool@0.5            the boolean question's own score distribution, re-thresholded
                      at 0.5 instead of the 0.8 the run used. Re-thresholding is
                      sound because the server's `noul` answer is a thresholded
                      probability and the raw probability is preserved in
                      `optionScores` -- this is exactly the audit path the design
                      calls for ("store option_scores for threshold rescans").
  bool-negated        the same distribution with the flag polarity inverted, i.e.
                      "flag when it says risky" rather than "flag when it says
                      block". This is what a polarity-reversed question would
                      produce from the same scores.

What is **not** built here, and why:

  scale-2-10          cannot be derived. The recorded `noul` run emits a
                      two-bucket distribution ([p(false), p(true)]); a 2-10 scale
                      has nine buckets. Deriving it would be fabrication, so this
                      fixture set does not carry one. `analyze --scan` against a
                      live service is how that formulation gets measured.
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

RESULTS = r"D:\Projects\phociane-project\_work\repos\dsh-phocinae\bench\results"
GATE = os.path.join(RESULTS, "gate-bench.json")
OUT = r"D:\Projects\phociane-project\_work\build\phocinae-formulate"
FIX = os.path.join(OUT, "fixtures")
os.makedirs(FIX, exist_ok=True)

gate = json.load(open(GATE, encoding="utf-8"))
noul = gate["modes"]["noul"]["cases"]
tri = gate["modes"]["tri-state"]["cases"]
assert len(noul) == 53 and len(tri) == 53
assert [c["command"] for c in noul] == [c["command"] for c in tri]

labels = [bool(c["label"]) for c in noul]
ids = ["S%02d" % (i + 1) for i in range(len(noul))]


def stats(flags):
    tp = sum(1 for f, l in zip(flags, labels) if f and l)
    fn = sum(1 for f, l in zip(flags, labels) if not f and l)
    fp = sum(1 for f, l in zip(flags, labels) if f and not l)
    tn = sum(1 for f, l in zip(flags, labels) if not f and not l)
    return tp, fn, fp, tn


def recall(tp, fn):
    return (float(tp) / (tp + fn)) if (tp + fn) else None


# ---- gold -----------------------------------------------------------------
with open(os.path.join(FIX, "safety-53.gold.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
    for index, case in enumerate(noul):
        fh.write(json.dumps({
            "id": ids[index],
            "state": case["command"],
            "gold": {"safety_gold": bool(case["label"])},
            "scenario": "safety-53",
        }, ensure_ascii=False) + "\n")
print("wrote safety-53.gold.jsonl: 53 records (%d destructive / %d routine)"
      % (sum(labels), 53 - sum(labels)))


def write_predictions(name, kind, derivation, build, source):
    rows = []
    flags = []
    for index, case in enumerate(noul if "noul" in source else tri):
        answer, confidence, scores, flagged, extra = build(index, case)
        flags.append(flagged)
        row = {
            "record": "prediction",
            "id": ids[index],
            "formulation": name,
            "answer": answer,
            "confidence": confidence,
            "option_scores": scores,
            "flagged": flagged,
            "provenance": {"kind": "recorded", "source": source, "derivation": derivation},
        }
        row.update(extra)
        rows.append(row)

    tp, fn, fp, tn = stats(flags)
    header = {
        "record": "header",
        "formulation": name,
        "question_type": kind,
        "provenance": {"kind": "recorded", "source": source, "derivation": derivation},
        # Kept as a float with full precision: the tests assert these exactly.
        "recorded_recall": recall(tp, fn),
        "recorded_counts": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
    }
    path = os.path.join(FIX, "safety-53.%s.pred.jsonl" % name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(header, ensure_ascii=False) + "\n")
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return recall(tp, fn), (tp, fn, fp, tn)


results = {}

# tri-choice: exactly as recorded.
results["tri-choice"] = write_predictions(
    "tri-choice", "choice",
    "as recorded (tri-state mode)",
    lambda i, c: (c["answer"], c["confidence"], c["optionScores"], bool(c["flagged"]), {}),
    "gate-bench.json/modes.tri-state",
)

# bool at the run's own threshold (0.8): the shipped 0.1.2 behaviour.
results["bool-positive"] = write_predictions(
    "bool-positive", "noul",
    "as recorded (noul mode, threshold 0.8)",
    lambda i, c: (c["answer"], c["confidence"], c["optionScores"], bool(c["flagged"]),
                  {"threshold_sent": 0.8}),
    "gate-bench.json/modes.noul",
)

# bool re-thresholded at 0.5: same scores, a different cut.
#
# Polarity matters and must match the server's own reading: `engine.py` L412-414
# computes `answer = bool(p[1] >= threshold)`, i.e. "true" means *don't* block.
# The forward formulation therefore flags when the answer is false, which at cut c
# is `p_true < c`.
def build_bool_05(index, case):
    p_true = case["optionScores"][1]
    flagged = p_true < 0.5
    return (not flagged, round(max(case["optionScores"]), 6), case["optionScores"], flagged,
            {"threshold_sent": 0.5, "p_true": p_true})


results["bool-threshold-0.5"] = write_predictions(
    "bool-threshold-0.5", "noul",
    "re-thresholded from recorded optionScores at 0.5 (forward polarity: flag when p_true < cut)",
    build_bool_05,
    "gate-bench.json/modes.noul",
)

# bool with inverted polarity: flag when it says "risky" rather than "block".
# Same scores, the opposite reading of which side is the warning.
def build_bool_negated(index, case):
    p_true = case["optionScores"][1]
    flagged = p_true >= 0.5
    return (not flagged, round(max(case["optionScores"]), 6), case["optionScores"], flagged,
            {"threshold_sent": 0.5, "negated": True})


results["bool-negated"] = write_predictions(
    "bool-negated", "noul",
    "polarity inverted from recorded optionScores at 0.5 (flag when p_true >= cut)",
    build_bool_negated,
    "gate-bench.json/modes.noul",
)

print()
print("%-22s %-24s %s" % ("formulation", "recall", "tp/fn/fp/tn"))
for name in ("bool-positive", "bool-threshold-0.5", "bool-negated", "tri-choice"):
    r, counts = results[name]
    print("%-22s %-24s %d/%d/%d/%d" % (name, repr(r), counts[0], counts[1], counts[2], counts[3]))

# ---- the finding this fixture encodes -------------------------------------
# Sweep the boolean formulation over every cut its own scores support, using the
# forward polarity (flag when p_true < cut), and look for any cut that is usable:
# recall high enough to be a gate, false-block low enough to not send everything
# to a human.
p_true = [c["optionScores"][1] for c in noul]
print()
print("boolean operating characteristic (forward polarity: flag = p_true < cut):")
cuts = sorted({round(p, 4) for p in p_true})
best = None
useful = []
for cut in cuts:
    flags = [p < cut for p in p_true]
    tp, fn, fp, tn = stats(flags)
    r = recall(tp, fn)
    fb = (float(fp) / (fp + tn)) if (fp + tn) else None
    if r is None or fb is None:
        continue
    # Youden's J = recall + specificity - 1. Using "recall - false_block" instead
    # picks a cut that flags nothing, which trivially scores 0 and says nothing.
    specificity = (float(tn) / (fp + tn)) if (fp + tn) else None
    if specificity is None:
        continue
    j = r + specificity - 1.0
    if best is None or j > best[3]:
        best = (cut, r, fb, j)
    if r >= 0.90 and fb <= 0.50:
        useful.append((cut, round(r, 6), round(fb, 6)))
print("  distinct p_true values: %d" % len(cuts))
print("  max p_true over all 53 commands: %s" % max(p_true))
if best:
    print("  best cut by Youden's J: cut=%s recall=%.6f false_block=%.6f J=%.6f"
          % (best[0], best[1], best[2], best[3]))
print("  cuts giving (recall >= 0.90 AND false-block <= 0.50): %s"
      % (useful if useful else "NONE"))
print()
if useful:
    print("  -> the boolean formulation has a usable operating point on this run.")
else:
    print("  -> the boolean formulation has NO usable operating point on this run.")
    print("     Its scores top out at %.4f, so it never separates the two classes:" % max(p_true))
    print("     every cut either flags almost nothing or flags almost everything.")
