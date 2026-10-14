"""Command-line interface.

    phocinae-formulate analyze --gold GOLD --predictions PRED [options]
    phocinae-formulate replay  --fixture NAME --expect EXPECT [options]
    phocinae-formulate doctor  --gold GOLD [--json]

Exit codes are documented in :mod:`phocinae_formulate.errors` and are the
contract for CI. In particular a finding exits ``1`` rather than ``0``, and
``replay`` refuses to run without ``--expect``: a replay without an expectation
verifies nothing.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

from . import __version__
from .errors import (
    FormulateError,
    EXIT_OK,
    EXIT_FINDING,
    EXIT_USAGE,
    EXIT_DATA,
    EXIT_EMPTY_SCAN,
    EXIT_EXPECT,
    EXIT_SKIP,
    resolve_exit_code,
)
from .gold import load_gold, check_gold_consistency
from .predictions import load_predictions
from .report import analyse, build_report, dumps, render_text

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FIXTURES = os.path.join(os.path.dirname(os.path.dirname(HERE)), "fixtures")


def _emit(text):
    stream = sys.stdout
    try:
        stream.reconfigure(encoding="utf-8", newline="\n")
    except (AttributeError, ValueError):
        pass
    stream.write(text)


def _parse_tau_grid(spec):
    if not spec:
        return None
    parts = spec.split(":")
    if len(parts) not in (2, 3):
        raise FormulateError("E_USAGE", "--tau-list wants start:stop[:step]", EXIT_USAGE)
    start = float(parts[0])
    stop = float(parts[1])
    step = float(parts[2]) if len(parts) == 3 else 0.05
    if step <= 0 or stop < start:
        raise FormulateError("E_USAGE", "--tau-list needs a positive step and stop >= start",
                             EXIT_USAGE)
    grid = []
    value = start
    while value <= stop + 1e-9:
        grid.append(round(value, 6))
        value += step
    return grid


def _load_tau_grid(args):
    if getattr(args, "tau_list", None):
        return _parse_tau_grid(args.tau_list)
    if getattr(args, "tau_grid_file", None):
        with open(args.tau_grid_file, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return None


def _write_reports(args, report):
    out_dir = getattr(args, "out_dir", None)
    if not out_dir:
        return []
    os.makedirs(out_dir, exist_ok=True)
    written = []
    json_path = os.path.join(out_dir, "report.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dumps(report))
    written.append(json_path)
    formats = (getattr(args, "format", None) or "json").split(",")
    if "md" in formats:
        md_path = os.path.join(out_dir, "report.md")
        with open(md_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_render_markdown(report))
        written.append(md_path)
    if "csv" in formats:
        csv_path = os.path.join(out_dir, "report.csv")
        with open(csv_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_render_csv(report))
        written.append(csv_path)
    return written


def _render_markdown(report):
    lines = ["# phocinae-formulate report", "",
             "- gold: `%s`" % report.get("gold"),
             "- sources: %s" % ", ".join(report.get("sources") or []),
             "- service: `%s`" % (report.get("service") or {}).get("state", "unknown"),
             "", "## Formulations", "",
             "| formulation | verdict | recall | missed allow | false block | tau |",
             "|---|---|---|---|---|---|"]
    for name in sorted(report.get("formulations") or {}):
        entry = report["formulations"][name]
        if entry.get("status") != "OK":
            lines.append("| %s | SKIP | - | - | - | - |" % name)
            continue
        lines.append("| `%s` | %s | %s | %s | %s | %s |" % (
            name, entry["decision"]["do"], _fmt(entry["recall"]),
            _fmt(entry["missed_allow"]), _fmt(entry["model_false_block"]),
            entry["tau"]["selected"]))
    lines += ["", "## Findings", ""]
    for line in report["decision"]["headline"]:
        lines.append("- %s" % line)
    return "\n".join(lines) + "\n"


def _render_csv(report):
    header = "formulation,verdict,recall,missed_allow,false_block,specificity,accuracy,tau,positives,negatives"
    rows = [header]
    for name in sorted(report.get("formulations") or {}):
        entry = report["formulations"][name]
        if entry.get("status") != "OK":
            rows.append("%s,SKIP,,,,,,,," % name)
            continue
        counts = entry["counts"]
        rows.append("%s,%s,%s,%s,%s,%s,%s,%s,%d,%d" % (
            name, entry["decision"]["do"], _fmt(entry["recall"]),
            _fmt(entry["missed_allow"]), _fmt(entry["model_false_block"]),
            _fmt(entry["specificity"]), _fmt(entry["accuracy"]),
            entry["tau"]["selected"], counts["positives"], counts["negatives"]))
    return "\n".join(rows) + "\n"


def _fmt(value):
    return "" if value is None else ("%.6f" % value)


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


def _cmd_analyze(args):
    gold = load_gold(args.gold)
    inconsistencies = check_gold_consistency(gold)
    if inconsistencies:
        for problem in inconsistencies:
            sys.stderr.write("ERROR %s\n" % problem)
        return EXIT_DATA

    decidable = [r for r in gold if r.decidable]
    if not decidable:
        sys.stderr.write("ERROR E_EMPTY_SCAN: gold has %d records and none is decidable\n"
                         % len(gold))
        return EXIT_EMPTY_SCAN

    if not args.predictions:
        sys.stderr.write(
            "ERROR E_NO_SOURCE: analyze needs --predictions. This tool measures\n"
            "      formulations from recorded answers; the live path (--scan) is not\n"
            "      implemented in 0.1.0 by design, because a scan that cannot run in CI\n"
            "      would be a check that never executes.\n")
        return EXIT_USAGE

    header, predictions = load_predictions(args.predictions,
                                           allow_synthetic=args.allow_synthetic)
    payload = analyse(
        gold, predictions,
        tau_grid=_load_tau_grid(args),
        bins=args.ece_bins,
    )

    reachable = sum(1 for v in payload["formulations"].values() if v.get("status") == "OK")
    statuses = [v.get("status") for v in payload["formulations"].values()]
    skipped = sum(1 for s in statuses if s == "SKIP")
    counts = {
        "formulations": len(payload["formulations"]),
        "ran": reachable,
        "skipped": skipped,
        "failed": 0,
        "gold_records": len(gold),
        "decidable_records": len(decidable),
        "undecidable_records": len(gold) - len(decidable),
        "predictions": len(predictions),
    }
    if reachable == 0:
        sys.stderr.write("ERROR E_EMPTY_SCAN: no formulation could be measured\n")
        return EXIT_EMPTY_SCAN

    report = build_report(
        payload,
        gold_path=os.path.abspath(args.gold),
        prediction_sources=[os.path.abspath(args.predictions)],
        counts=counts,
        service_state={"state": "not_contacted",
                       "detail": "analysis ran from recorded predictions"},
    )

    written = _write_reports(args, report)
    if args.json or written:
        _emit(dumps(report))
    else:
        _emit(render_text(report))

    if payload["decision"]["any_dont_ship"]:
        return EXIT_FINDING
    return EXIT_OK


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def _resolve_expectation(expectation, report):
    """Evaluate an ``--expect`` file against a report. Returns a list of failures."""
    failures = []
    checks = expectation.get("checks")
    if not isinstance(checks, list) or not checks:
        return ["the expectation file has no 'checks'; a replay without assertions "
                "verifies nothing"]

    for index, check in enumerate(checks):
        where = "checks[%d]" % index
        kind = check.get("kind")
        name = check.get("formulation")
        entry = (report.get("formulations") or {}).get(name)
        if kind == "formulation_verdict":
            if entry is None or entry.get("status") != "OK":
                failures.append("%s: formulation %r was not measured" % (where, name))
                continue
            expected = check.get("expect")
            actual = entry["decision"]["do"]
            if actual != expected:
                failures.append("%s: %s verdict is %r, expected %r"
                                % (where, name, actual, expected))
            continue
        if kind == "metric":
            if entry is None or entry.get("status") != "OK":
                failures.append("%s: formulation %r was not measured" % (where, name))
                continue
            path = check.get("path")
            expected = check.get("expect")
            if path == "recall":
                actual = entry.get("recall")
            elif path == "tau":
                actual = entry["tau"]["selected"]
            elif path == "missed_allow":
                actual = entry.get("missed_allow")
            elif path == "model_false_block":
                actual = entry.get("model_false_block")
            else:
                failures.append("%s: unknown metric path %r" % (where, path))
                continue
            if check.get("exact"):
                if actual != expected:
                    failures.append("%s: %s.%s is %r, expected exactly %r"
                                    % (where, name, path, actual, expected))
            else:
                if actual is None or expected is None or abs(float(actual) - float(expected)) > float(
                        check.get("tolerance", 1e-9)):
                    failures.append("%s: %s.%s is %r, expected %r"
                                    % (where, name, path, actual, expected))
            continue
        if kind == "headline_contains":
            needle = check.get("text")
            if needle not in "\n".join(report["decision"]["headline"]):
                failures.append("%s: headline does not contain %r; got %r"
                                % (where, needle, report["decision"]["headline"]))
            continue
        if kind == "any_dont_ship":
            if bool(report["decision"]["any_dont_ship"]) != bool(check.get("expect")):
                failures.append("%s: any_dont_ship is %r, expected %r"
                                % (where, report["decision"]["any_dont_ship"], check.get("expect")))
            continue
        if kind == "overall_verdict":
            if report["decision"]["do"] != check.get("expect"):
                failures.append("%s: overall verdict is %r, expected %r"
                                % (where, report["decision"]["do"], check.get("expect")))
            continue
        if kind == "contains_text":
            blob = dumps(report)
            if check.get("text") not in blob:
                failures.append("%s: report does not contain %r" % (where, check.get("text")))
            continue
        failures.append("%s: unknown check kind %r" % (where, kind))
    return failures


def _cmd_replay(args):
    fixture_dir = args.fixture_path or DEFAULT_FIXTURES
    name = args.fixture
    gold_path = os.path.join(fixture_dir, "%s.gold.jsonl" % name)
    if not os.path.isfile(gold_path):
        candidates = sorted(
            f[: -len(".gold.jsonl")] for f in os.listdir(fixture_dir)
            if f.endswith(".gold.jsonl")
        ) if os.path.isdir(fixture_dir) else []
        raise FormulateError(
            "E_FIXTURE", "no fixture named %r under %r (available: %s)"
            % (name, fixture_dir, ", ".join(candidates) or "none"), EXIT_USAGE)

    gold = load_gold(gold_path)
    files = sorted(
        f for f in os.listdir(fixture_dir)
        if f.startswith("%s." % name) and f.endswith(".pred.jsonl")
    )
    if not files:
        raise FormulateError("E_FIXTURE", "no predictions for fixture %r under %r"
                             % (name, fixture_dir), EXIT_USAGE)

    predictions = []
    sources = []
    for filename in files:
        path = os.path.join(fixture_dir, filename)
        _header, rows = load_predictions(path)
        predictions.extend(rows)
        sources.append(path)

    payload = analyse(gold, predictions, tau_grid=_load_tau_grid(args), bins=args.ece_bins)
    counts = {
        "formulations": len(payload["formulations"]),
        "ran": sum(1 for v in payload["formulations"].values() if v.get("status") == "OK"),
        "skipped": sum(1 for v in payload["formulations"].values() if v.get("status") == "SKIP"),
        "failed": 0,
        "gold_records": len(gold),
        "predictions": len(predictions),
    }
    report = build_report(
        payload,
        gold_path=os.path.abspath(gold_path),
        prediction_sources=sources,
        counts=counts,
        service_state={"state": "not_contacted", "detail": "replayed from recordings"},
    )

    if not args.expect:
        sys.stderr.write(
            "ERROR E_NO_EXPECT: replay requires --expect. A replay without an\n"
            "      expectation reproduces numbers without checking them.\n")
        return EXIT_USAGE

    with open(args.expect, "r", encoding="utf-8") as handle:
        expectation = json.load(handle)
    failures = _resolve_expectation(expectation, report)
    report["expectations"] = {
        "file": os.path.abspath(args.expect),
        "passed": not failures,
        "failures": failures,
    }

    written = _write_reports(args, report)
    if args.json or written:
        _emit(dumps(report))
    else:
        _emit(render_text(report))
    if failures:
        for failure in failures:
            sys.stderr.write("EXPECTATION FAILED %s\n" % failure)
        return EXIT_EXPECT
    if payload["decision"]["any_dont_ship"]:
        return EXIT_FINDING
    return EXIT_OK


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def _cmd_doctor(args):
    gold = load_gold(args.gold)
    problems = check_gold_consistency(gold)
    decidable = [r for r in gold if r.decidable]
    positives = sum(1 for r in decidable if r.positive)
    report = {
        "gold": os.path.abspath(args.gold),
        "records": len(gold),
        "decidable": len(decidable),
        "undecidable": len(gold) - len(decidable),
        "positives": positives,
        "negatives": len(decidable) - positives,
        "problems": [str(p) for p in problems],
    }
    if args.json:
        _emit(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    else:
        _emit("gold: %s\n" % report["gold"])
        _emit("records=%d decidable=%d undecidable=%d positives=%d negatives=%d\n"
              % (report["records"], report["decidable"], report["undecidable"],
                 report["positives"], report["negatives"]))
        for problem in report["problems"]:
            _emit("PROBLEM %s\n" % problem)
    if not decidable:
        return EXIT_EMPTY_SCAN
    return EXIT_DATA if problems else EXIT_OK


# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="phocinae-formulate",
        description="Measure which way of asking a decision model actually works. "
                    "Runs entirely from recorded predictions; no service needed.",
    )
    parser.add_argument("--version", action="version", version="phocinae_formulate %s" % __version__)
    sub = parser.add_subparsers(dest="command")

    def common(p):
        p.add_argument("--out-dir", help="write report.json (and .md/.csv) here")
        p.add_argument("--format", default="json",
                       help="comma-separated: json,md,csv (only used with --out-dir)")
        p.add_argument("--json", action="store_true", help="write the report to stdout")
        p.add_argument("--ece-bins", type=int, default=10)
        p.add_argument("--tau-list", help="tau grid as start:stop[:step], e.g. 0.30:0.60:0.05")
        p.add_argument("--tau-grid-file", help="JSON file holding an explicit tau grid")

    analyze = sub.add_parser("analyze", help="analyse recorded predictions against a gold set")
    analyze.add_argument("--gold", required=True)
    analyze.add_argument("--predictions", required=True)
    analyze.add_argument("--allow-synthetic", action="store_true",
                         help="permit synthetic provenance (tests only)")
    common(analyze)
    analyze.set_defaults(handler=_cmd_analyze)

    replay = sub.add_parser("replay", help="replay a shipped fixture and check it against an expectation")
    replay.add_argument("--fixture", required=True)
    replay.add_argument("--fixture-path", default=None)
    replay.add_argument("--expect", required=True)
    common(replay)
    replay.set_defaults(handler=_cmd_replay)

    doctor = sub.add_parser("doctor", help="inspect a gold set before measuring with it")
    doctor.add_argument("--gold", required=True)
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=_cmd_doctor)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.handler(args)
    except FormulateError as exc:
        sys.stderr.write("ERROR %s\n" % exc)
        return exc.exit_code
    except BrokenPipeError:
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
