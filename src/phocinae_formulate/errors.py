"""Errors and exit codes.

The exit-code vocabulary is the contract for CI, and it is deliberately not
collapsed: "the tool could not run", "the tool ran and found something", and
"the assertions I asked for failed" are three different facts.

    0   ran, no finding
    1   ran, a finding exists -- some formulation must not be used
    2   usage error
    3   input data error (schema, duplicate id, inconsistent gold)
    4   empty scan: nothing decidable to measure
    5   `--expect` assertion failed
    6   skipped: the decision service was not reachable

Priority is fixed and total, implemented in exactly one place::

    6 > 4 > 3 > 2 > 5 > 1 > 0

Why ``5`` outranks ``1``: a failed assertion means the tool itself is broken,
while a finding means the tool worked and the answer is "do not use this".
A broken tool is the more serious fact.

Why ``1`` is driven by *any* formulation rather than the winner: this tool exists
to identify formulations that must not be used. If only the winner counted, a run
where the boolean formulation fails and the scale formulation succeeds would
return ``0`` -- and "do not use the boolean" is precisely the finding that would
be swallowed.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_USAGE = 2
EXIT_DATA = 3
EXIT_EMPTY_SCAN = 4
EXIT_EXPECT = 5
EXIT_SKIP = 6

EXIT_CODES = (
    EXIT_OK,
    EXIT_FINDING,
    EXIT_USAGE,
    EXIT_DATA,
    EXIT_EMPTY_SCAN,
    EXIT_EXPECT,
    EXIT_SKIP,
)

#: Highest priority first. The first code present wins.
_PRIORITY = (EXIT_SKIP, EXIT_EMPTY_SCAN, EXIT_DATA, EXIT_USAGE, EXIT_EXPECT, EXIT_FINDING, EXIT_OK)


class FormulateError(Exception):
    """A usage or data error. ``args[0]`` is the machine-readable reason code."""

    def __init__(self, reason_code, message, exit_code=EXIT_DATA):
        self.reason_code = reason_code
        self.message = message
        self.exit_code = exit_code
        super(FormulateError, self).__init__(reason_code, message)

    def __str__(self):
        return "%s: %s" % (self.reason_code, self.message)


def resolve_exit_code(codes):
    """Combine candidate exit codes by the fixed priority."""
    present = set(codes)
    for code in _PRIORITY:
        if code in present:
            return code
    return EXIT_OK
