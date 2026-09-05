from __future__ import annotations

"""What a result is worth: checked, partly read, not supported, or not run.

A count of zero means two very different things. A check that ran and found
nothing is a clean result. A check that could not run -- no cross-reference, no
index, a program in a language this cannot read -- also reports zero, and
reads as the same clean result. Six of lint's checks were in that state: they
printed "check skipped" and returned an empty list, and the summary recorded
`count: 0` alongside the checks that had actually looked.

The states are deliberately few, and none of them is a percentage. A number
without a basis invites the reader to average away the part that says "this was
not examined".

    CHECKED         examined within the range this tool supports
    PARTIAL         examined, and part of the input could not be interpreted
    UNSUPPORTED     the input is a form this tool does not read
    TRUNCATED       the search stopped at a limit before it was exhausted
    NOT_EVALUATED   the check could not run; its prerequisite is missing
    NO_MEASUREMENT  only a file was read; the question needs a live value

A result carries why, and what to do about it, because "not evaluated" without
the missing prerequisite just moves the puzzle to the reader.
"""

from dataclasses import dataclass, field


CHECKED = "checked"
PARTIAL = "partial"
UNSUPPORTED = "unsupported"
TRUNCATED = "truncated"
NOT_EVALUATED = "not_evaluated"
NO_MEASUREMENT = "no_measurement"

STATES = (CHECKED, PARTIAL, UNSUPPORTED, TRUNCATED, NOT_EVALUATED, NO_MEASUREMENT)

# A result in any of these states does not support the sentence "nothing was
# found here". Anything that reports a count has to say so alongside it.
INCONCLUSIVE = (PARTIAL, UNSUPPORTED, TRUNCATED, NOT_EVALUATED, NO_MEASUREMENT)

LABELS = {
    CHECKED: "checked within the supported range",
    PARTIAL: "checked, part of the input not interpreted",
    UNSUPPORTED: "not supported",
    TRUNCATED: "search stopped at a limit",
    NOT_EVALUATED: "not evaluated",
    NO_MEASUREMENT: "no measured value; file only",
}

# The same six states, for readers who work in Japanese. A translation kept
# beside the original rather than in whichever command happens to print it:
# two commands wording "not evaluated" differently is how a reader learns to
# skip the line.
LABELS_JA = {
    CHECKED: "対応範囲内で確認",
    PARTIAL: "一部未解釈",
    UNSUPPORTED: "未対応",
    TRUNCATED: "探索打切り",
    NOT_EVALUATED: "未評価",
    NO_MEASUREMENT: "実測値なし（ファイルのみ）",
}


def label_for(state: str, ja: bool = False) -> str:
    return (LABELS_JA if ja else LABELS)[state]


@dataclass
class AnalysisState:
    """The state of one result, and the reason it is not simply "checked"."""

    state: str = CHECKED
    reason: str = ""
    next_step: str = ""
    detail: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown analysis state: {self.state}")

    @property
    def conclusive(self) -> bool:
        """Whether a count from this result can be read as the whole answer."""
        return self.state == CHECKED

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "state": self.state,
            "label": LABELS[self.state],
            "label_ja": LABELS_JA[self.state],
        }
        if self.reason:
            out["reason"] = self.reason
        if self.next_step:
            out["next_step"] = self.next_step
        if self.detail:
            out["detail"] = dict(self.detail)
        return out

    def line(self, subject: str = "", ja: bool = False) -> str:
        head = f"{subject}: " if subject else ""
        text = f"{head}{label_for(self.state, ja)}"
        if self.reason:
            text += f" -- {self.reason}"
        if self.next_step:
            text += f"; next: {self.next_step}"
        return text


def not_evaluated(reason: str, next_step: str = "") -> AnalysisState:
    return AnalysisState(NOT_EVALUATED, reason=reason, next_step=next_step)


def checked(detail: dict[str, object] | None = None) -> AnalysisState:
    return AnalysisState(CHECKED, detail=detail or {})


def worst(states: list[AnalysisState]) -> AnalysisState:
    """The state a set of results should be reported under.

    One check that could not run makes the set inconclusive, however many
    others were clean: a summary that hides it is the failure this exists to
    stop.
    """
    order = {CHECKED: 0, NO_MEASUREMENT: 1, TRUNCATED: 2, PARTIAL: 3, UNSUPPORTED: 4, NOT_EVALUATED: 5}
    if not states:
        return AnalysisState(NOT_EVALUATED, reason="nothing was examined")
    return max(states, key=lambda s: order[s.state])


def summarise(states: dict[str, AnalysisState]) -> dict[str, object]:
    """Counts per state, and the names that are not conclusive."""
    counts: dict[str, int] = {}
    for state in states.values():
        counts[state.state] = counts.get(state.state, 0) + 1
    return {
        "by_state": counts,
        "inconclusive": sorted(
            name for name, state in states.items() if state.state in INCONCLUSIVE
        ),
    }
