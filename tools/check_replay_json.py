# -*- coding: utf-8 -*-
"""Assert what the replay JSON must contain, in Python rather than in `grep`.

The two `grep -q` lines this replaces were the wrong tool, and they failed in a
way worth recording.

`grep -q` exits 1 when it finds nothing, and a GitHub Actions `run:` block runs
under `set -e`. So a *check that reports a problem* and a *check that is broken*
produce exactly the same red step, and the log gives no hint which one happened:
the step printed `replay exit code: 1 (1 = a finding was reported)` — meaning the
replay had done precisely the right thing — and then died on the next line
without saying what it had looked for.

This script instead reads the JSON, asks for the keys by name, and prints what it
found. It exits 0 when the JSON says what it must, and 1 with a specific message
when it does not, so red means one thing.

Usage:
    python tools/check_replay_json.py replay.json
"""

from __future__ import annotations

import io
import json
import os
import sys


def _read_report(path, stream):
    """Read the replay report however the shell happened to write it.

    This reads bytes and decodes, rather than opening in text mode, because the
    producer of this file is a *shell redirection* and shells disagree:

    * `bash` writes UTF-8, no BOM;
    * PowerShell's `>` writes UTF-16LE with a BOM;
    * a BOM in front of otherwise valid UTF-8 JSON makes `json.load` raise
      "Unexpected UTF-8 BOM".

    A check that only understands one of those reports the author's shell instead
    of the replay's state. Diagnosing exactly that cost a CI cycle here, so the
    bytes are inspected instead: a BOM decides the encoding, otherwise UTF-8 is
    tried and UTF-16 attempted only if that fails.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw == b"":
        raise ValueError("the file is empty, so the replay wrote nothing to stdout "
                         "(a consumer reading a pipe sees this when the writer "
                         "does not flush)")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    elif raw[:3] == b"\xef\xbb\xbf":
        text = raw[3:].decode("utf-8")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-16")
    return json.loads(text.lstrip("\ufeff \t\r\n"))


def main(argv):
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if len(argv) != 2:
        stream.write("usage: check_replay_json.py <replay.json>\n")
        return 2
    path = argv[1]
    if not os.path.isfile(path):
        stream.write("FAIL the replay produced no JSON at %s\n" % path)
        return 1

    try:
        report = _read_report(path, stream)
    except ValueError as exc:
        stream.write("FAIL %s could not be read as JSON: %s\n" % (path, exc))
        return 1
    if not isinstance(report, dict):
        stream.write("FAIL %s holds a %s, not a report object\n"
                     % (path, type(report).__name__))
        return 1

    failures = []

    expectations = report.get("expectations")
    if not isinstance(expectations, dict):
        failures.append("the report carries no `expectations` object")
    else:
        if expectations.get("passed") is not True:
            failures.append("`expectations.passed` is %r, and the replay must pass its own "
                            "recorded expectations" % (expectations.get("passed"),))
        listed = expectations.get("failures")
        if listed:
            # Name them: a count alone sends the reader back to the file.
            failures.append("the replay reported expectation failures: %s"
                            % json.dumps(listed, ensure_ascii=False)[:400])

    decision = report.get("decision")
    if not isinstance(decision, dict):
        failures.append("the report carries no `decision` object")
    else:
        # The point of this project: the replay of the 0.1.2 defect must SAY the
        # formulation is unusable rather than merely scoring it low.
        if decision.get("do") != "don't_ship":
            failures.append("`decision.do` is %r, expected \"don't_ship\""
                            % (decision.get("do"),))
        headline = decision.get("headline") or []
        if not any("DO NOT USE" in str(line) for line in headline):
            failures.append("no headline names a formulation as DO NOT USE; got %s"
                            % json.dumps(headline, ensure_ascii=False)[:300])
        if not decision.get("dont_ship_formulations"):
            failures.append("`dont_ship_formulations` is empty, so nothing was rejected")

    if failures:
        for failure in failures:
            stream.write("FAIL %s\n" % failure)
        return 1

    stream.write("OK the replay JSON says what it must: decision=%s, rejected=%s\n"
                 % (decision.get("do"), ", ".join(decision.get("dont_ship_formulations") or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
