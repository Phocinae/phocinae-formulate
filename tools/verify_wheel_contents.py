"""Open the wheel that is about to be uploaded and check what is inside it.

Runs before upload, on the exact file. "The source tree passes" and "the
published artefact is correct" are two different claims, and this project's
family has shipped a wheel that was missing a data file its whole test suite
passed without.

    python tools/verify_wheel_contents.py dist-upload

Passing more than one wheel is an error: picking one silently is how a check
ends up inspecting the previous release.
"""

from __future__ import annotations

import io
import os
import sys
import zipfile

REQUIRED = [
    "phocinae_formulate/fixtures/safety-53.gold.jsonl",
    "phocinae_formulate/fixtures/replay-0.1.3.expect.json",
    "phocinae_formulate/__init__.py",
    "phocinae_formulate/__main__.py",
    "phocinae_formulate/calibration.py",
    "phocinae_formulate/cli.py",
    "phocinae_formulate/errors.py",
    "phocinae_formulate/formulations.py",
    "phocinae_formulate/gold.py",
    "phocinae_formulate/metrics.py",
    "phocinae_formulate/predictions.py",
    "phocinae_formulate/report.py",
    "phocinae_formulate/taucan.py",
    "phocinae_formulate/verdict.py",
]

# fixtures/ is deliberately packaged (the quick-start replay must work for a pip
# user), so it is NOT forbidden; tests/ still is.
FORBIDDEN_SUBSTRINGS = ("__pycache__", "/tests/", "\\tests\\")
FORBIDDEN_SUFFIXES = (".pyc", ".pyo")

EXPECTED_VERSION = "0.1.3"


def wheels_in(target):
    if os.path.isfile(target):
        return [target]
    if not os.path.isdir(target):
        return []
    return [os.path.join(target, n) for n in sorted(os.listdir(target)) if n.endswith(".whl")]


def main(argv):
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    target = argv[1] if len(argv) > 1 else "dist-upload"
    found = wheels_in(target)
    if not found:
        stream.write("FAIL: no wheel found under %r\n" % target)
        return 1
    if len(found) > 1:
        stream.write("FAIL: %d wheels under %r; refusing to guess which one is being "
                     "uploaded:\n" % (len(found), target))
        for path in found:
            stream.write("  %s\n" % path)
        return 1

    path = found[0]
    stream.write("inspecting %s\n" % path)
    failures = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for member in REQUIRED:
            if member not in names:
                failures.append("missing required member: %s" % member)
        for name in names:
            if any(token in name for token in FORBIDDEN_SUBSTRINGS):
                failures.append("ships test or fixture data: %s" % name)
            if name.endswith(FORBIDDEN_SUFFIXES):
                failures.append("ships bytecode: %s" % name)

        metadata_name = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        if metadata_name is None:
            failures.append("no METADATA found")
        else:
            metadata = archive.read(metadata_name).decode("utf-8")
            version = None
            for line in metadata.split("\n"):
                if line.startswith("Version: "):
                    version = line.split(": ", 1)[1].strip()
            stream.write("metadata version: %s\n" % version)
            if version != EXPECTED_VERSION:
                failures.append("METADATA Version is %r, expected %r" % (version, EXPECTED_VERSION))

            # A dependency guarded by an `extra ==` marker is not a runtime
            # dependency; an unconditional Requires-Dist is.
            unconditional = []
            for line in metadata.split("\n"):
                if not line.startswith("Requires-Dist:"):
                    continue
                requirement = line.split(":", 1)[1].strip()
                if "extra ==" in requirement:
                    stream.write("optional extra dependency: %s\n" % requirement)
                    continue
                unconditional.append(requirement)
            if unconditional:
                failures.append("unconditional runtime dependency: %s" % unconditional)

            init = archive.read("phocinae_formulate/__init__.py").decode("utf-8")
            for line in init.split("\n"):
                if line.startswith("__version__"):
                    constant = line.split("=", 1)[1].strip().strip('"').strip("'")
                    stream.write("__version__: %s\n" % constant)
                    if constant != version:
                        failures.append("__version__ %r != METADATA %r" % (constant, version))
                    break

        stream.write("members: %d\n" % len(names))

    if failures:
        for failure in failures:
            stream.write("FAIL %s\n" % failure)
        return 1
    stream.write("OK: %d required members, no test data, no bytecode, no runtime dependencies\n"
                 % len(REQUIRED))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
