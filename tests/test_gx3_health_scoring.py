from __future__ import annotations

"""Not looking must not score better than looking.

The Doctor deducts from 100 for each finding, so "no findings" and "no checks
ran" produced the same number. On a project with nothing built, one of eight
checks was evaluated and every dimension came out 100/100 -- above the 96 of a
project where seven checks ran and found something.

The word INCOMPLETE was already on the report. Nobody reads a scorecard for its
adjective; they read the numbers, and the numbers said the opposite.

A dimension is scored only when every check that speaks for it was evaluated.
One check out of three is a floor, not a score. Supplemental checks are exempt
by the same decision that lets them not block the verdict: link-range needs a
cross-project map that a single-project diagnosis should not require.
"""

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_audit import (
    DIMENSIONS,
    SUPPLEMENTAL_DOCTOR_CHECKS,
    health_label,
    health_scores,
    scored_dimensions,
)


ALL_CHECKS = {
    "duplicate-coil",
    "multi-writer",
    "alarm-quality",
    "unused-device",
    "comment-conflict",
    "link-range",
    "io-comment-gap",
    "constant-chain",
}


def test_a_dimension_with_an_unevaluated_check_is_not_scored() -> None:
    # multi-writer speaks for Maintainability. Without it, a clean
    # duplicate-coil result is not a maintainability score.
    scores = health_scores([], inconclusive={"multi-writer"})
    assert scores["Maintainability"] is None, scores
    assert scores["Change safety"] is None, scores


def test_nothing_evaluated_scores_nothing() -> None:
    scores = health_scores([], inconclusive=ALL_CHECKS)
    assert all(value is None for value in scores.values()), scores
    assert health_label(scores) == "NOT ASSESSED", health_label(scores)


def test_everything_evaluated_and_clean_is_a_full_score() -> None:
    scores = health_scores([], inconclusive=set())
    assert all(value == 100 for value in scores.values()), scores
    assert health_label(scores) == "GOOD"


def test_a_finding_lowers_the_dimensions_it_belongs_to() -> None:
    findings = [{"check": "multi-writer", "severity": "high"}]
    scores = health_scores(findings, inconclusive=set())
    assert scores["Maintainability"] is not None and scores["Maintainability"] < 100, scores
    # multi-writer says nothing about documentation.
    assert scores["Documentation"] == 100, scores


def test_a_supplemental_check_does_not_blank_a_dimension() -> None:
    # link-range needs a cross-project link map. A single-project diagnosis
    # should still report, which is the existing decision this follows.
    assert SUPPLEMENTAL_DOCTOR_CHECKS == {"link-range"}, SUPPLEMENTAL_DOCTOR_CHECKS
    scores = health_scores([], inconclusive={"link-range"})
    assert all(value is not None for value in scores.values()), scores


def test_examining_less_never_scores_higher() -> None:
    """The failure stated as an invariant.

    Whatever the findings, a run that evaluated fewer checks must not come out
    with a higher number on any dimension than the same run with more.
    """
    findings = [{"check": "multi-writer", "severity": "high"}]
    examined = health_scores(findings, inconclusive=set())
    unexamined = health_scores([], inconclusive={"multi-writer", "unused-device"})
    for dimension in DIMENSIONS:
        less = unexamined[dimension]
        more = examined[dimension]
        if less is None or more is None:
            continue
        assert less <= more, (dimension, less, more)


def test_the_report_prints_a_dash_rather_than_a_number() -> None:
    from gx3cli.gx3_audit import print_project_health

    report = {
        "health": "INCOMPLETE",
        "provisional_health": "NOT ASSESSED",
        "scores": {name: None for name in DIMENSIONS},
        "analysis": {
            "evaluated": 0,
            "total_checks": 8,
            "core_inconclusive": ["multi-writer"],
            "supplemental_inconclusive": [],
        },
        "total_findings": 0,
        "top_risks": [],
        "observations": [],
        "score_kind": "heuristic",
    }
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        with contextlib.suppress(Exception):
            print_project_health(report)
    body = out.getvalue()
    assert "/100" not in body, body
    assert "no health to report" in body, body


def main() -> int:
    test_a_dimension_with_an_unevaluated_check_is_not_scored()
    test_nothing_evaluated_scores_nothing()
    test_everything_evaluated_and_clean_is_a_full_score()
    test_a_finding_lowers_the_dimensions_it_belongs_to()
    test_a_supplemental_check_does_not_blank_a_dimension()
    test_examining_less_never_scores_higher()
    test_the_report_prints_a_dash_rather_than_a_number()
    print("health scoring checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
