"""phocinae-formulate — a formulation benchmark for a decision model.

The model's accuracy depends far more on *how the question is phrased* than on
what it is being asked about. Measured on 53 labelled commands:

    "Should this be blocked?"   (boolean)    recall 0.00  -- it passed all 31
    allow / ask / deny          (three-way)  recall 0.74
    harmless / risky / destructive (scale)   recall 0.97

This package answers "which formulation should I use, and should I build this at
all?" with numbers instead of intuition, and it can do so entirely offline from
recorded predictions.

Design contract:

  * ``summary`` is the plain result. JSON to stdout, human text as the fallback.
  * The formulation that cannot be used is reported even when another one works.
  * Exit code ``1`` means a finding (do not ship this formulation), not a crash.
  * An empty scan is a failure (``4``), not a success.
  * A skipped run is ``6``, because "did not run" is not "passed".
"""

from __future__ import annotations

from .errors import (
    EXIT_OK,
    EXIT_FINDING,
    EXIT_USAGE,
    EXIT_DATA,
    EXIT_EMPTY_SCAN,
    EXIT_EXPECT,
    EXIT_SKIP,
    EXIT_CODES,
    FormulateError,
    resolve_exit_code,
)
from .formulations import FORMULATIONS, FORMULATION_ORDER, all_formulations, get_formulation
from .gold import Record, load_gold, check_gold_consistency
from .metrics import Confusion, confusion, percentile, undecided
from .predictions import Prediction, load_predictions
from .taucan import tau_sweep
from .verdict import Decision, decide_formulation, overall, rank
from .report import analyse, build_report, render_text

__version__ = "0.1.2"

__all__ = [
    "__version__",
    # errors / exit codes
    "FormulateError",
    "EXIT_OK",
    "EXIT_FINDING",
    "EXIT_USAGE",
    "EXIT_DATA",
    "EXIT_EMPTY_SCAN",
    "EXIT_EXPECT",
    "EXIT_SKIP",
    "EXIT_CODES",
    "resolve_exit_code",
    # data
    "Record",
    "load_gold",
    "check_gold_consistency",
    "Prediction",
    "load_predictions",
    "FORMULATIONS",
    "FORMULATION_ORDER",
    "all_formulations",
    "get_formulation",
    # analysis
    "Confusion",
    "confusion",
    "percentile",
    "undecided",
    "tau_sweep",
    "decide_formulation",
    "Decision",
    "overall",
    "rank",
    "analyse",
    "build_report",
    "render_text",
]
