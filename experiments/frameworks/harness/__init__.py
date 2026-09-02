"""Shared, dependency-free harness for the cross-framework phantom-transition matrix.

Every runtime experiment answers the same question with the same scenario:

    A scripted "LLM" emits a state-mutating tool call (advance_phase to DISCOVERY)
    followed by a long reply. The caller barges in at a controlled offset before
    the reply finishes. What phase is the session in afterwards?

This package holds the scenario, the seeded timing schedule, the per-run record,
the classification rule and the report writers. It imports nothing outside the
standard library so the repository's CI can test it without any framework
installed. Each runtime folder next to it adapts the scenario to that runtime's
own testing utilities and writes RunRecords through this package.
"""

from .protocol import (
    INITIAL_PHASE,
    REPLY_TEXT,
    TARGET_PHASE,
    TOOL_NAME,
    USER_UTTERANCE,
    Config,
    NotMeasured,
    RunRecord,
    Schedule,
    Summary,
    classify_run,
    make_schedule,
    summarise,
)
from .report import render_manifest, render_matrix_md, render_matrix_tex

__all__ = [
    "INITIAL_PHASE",
    "REPLY_TEXT",
    "TARGET_PHASE",
    "TOOL_NAME",
    "USER_UTTERANCE",
    "Config",
    "NotMeasured",
    "RunRecord",
    "Schedule",
    "Summary",
    "classify_run",
    "make_schedule",
    "summarise",
    "render_manifest",
    "render_matrix_md",
    "render_matrix_tex",
]
