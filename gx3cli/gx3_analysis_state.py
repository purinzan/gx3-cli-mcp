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


# The five stages an answer passes through, from #49. A result that is not
# "checked" was stopped at one of them, and which one decides what to do next:
# a program in a language this does not read is not fixed by raising a depth
# limit, and neither is a rung whose wiring could not be folded into a
# condition.
DISCOVERY = "discovery"
DECODE = "decode"
TOPOLOGY = "topology"
SEMANTICS = "semantics"
REACH = "reach"

STAGES = (DISCOVERY, DECODE, TOPOLOGY, SEMANTICS, REACH)

STAGE_LABELS = {
    DISCOVERY: "containers and programs",
    DECODE: "instructions and operands",
    TOPOLOGY: "wiring and enable logic",
    SEMANTICS: "execution meaning (SET/RST, timers, edges, MC, jumps, multiple writes)",
    REACH: "how far the search went",
}

STAGE_LABELS_JA = {
    DISCOVERY: "コンテナとプログラムの発見",
    DECODE: "命令とオペランドの復元",
    TOPOLOGY: "配線と成立論理の復元",
    SEMANTICS: "実行上の意味（SET/RST・タイマ・立上り・MC・ジャンプ・複数書込）",
    REACH: "問われた範囲までの追跡",
}


def stage_label(stage: str, ja: bool = False) -> str:
    return (STAGE_LABELS_JA if ja else STAGE_LABELS).get(stage, stage)


@dataclass
class AnalysisState:
    """The state of one result, and the reason it is not simply "checked"."""

    state: str = CHECKED
    reason: str = ""
    next_step: str = ""
    detail: dict[str, object] = field(default_factory=dict)
    # Which of the five stages stopped this. Empty when nothing did.
    stage: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown analysis state: {self.state}")
        if self.stage and self.stage not in STAGES:
            raise ValueError(f"unknown analysis stage: {self.stage}")
        # NO_MEASUREMENT is the one state the five stages cannot describe. They
        # are stages of reading the project file, and this state says the file
        # was read through to the end -- what is missing is a value that only a
        # running PLC has. Naming a stage here would point the reader at the
        # decoder when the answer is to capture the value.
        #
        # The rule behind the stage requirement is that a result which is not
        # "checked" has to say what to do about it, so this state carries that
        # obligation as next_step instead of dropping it.
        if self.state == NO_MEASUREMENT:
            if not self.next_step:
                raise ValueError(
                    f"'{NO_MEASUREMENT}' has to say how the value would be obtained: set next_step"
                )
        elif self.state != CHECKED and not self.stage:
            raise ValueError(
                f"a result that is not '{CHECKED}' has to say which stage stopped it: {self.state}"
            )

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
        if self.stage:
            out["stage"] = self.stage
            out["stage_label"] = STAGE_LABELS[self.stage]
            out["stage_label_ja"] = STAGE_LABELS_JA[self.stage]
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
        if self.stage:
            text += f" [{stage_label(self.stage, ja)}]"
        if self.reason:
            text += f" -- {self.reason}"
        if self.next_step:
            text += f"; next: {self.next_step}"
        return text


def not_evaluated(reason: str, next_step: str = "", stage: str = DISCOVERY) -> AnalysisState:
    """A check that could not run. By default its prerequisite was missing.

    DISCOVERY is the honest default: "the thing this needed was not there" is a
    statement about what could be found, not about what could be decoded.
    """
    return AnalysisState(NOT_EVALUATED, reason=reason, next_step=next_step, stage=stage)


def checked(detail: dict[str, object] | None = None) -> AnalysisState:
    return AnalysisState(CHECKED, detail=detail or {})


def no_measurement(
    reason: str, next_step: str, detail: dict[str, object] | None = None
) -> AnalysisState:
    """The file was read; the question needs a value the file does not hold.

    Distinct from NOT_EVALUATED, which says a prerequisite was missing and the
    check never ran, and from PARTIAL, which says the input could not be fully
    interpreted. Here everything readable was read and the answer still depends
    on a measurement.
    """
    return AnalysisState(
        NO_MEASUREMENT, reason=reason, next_step=next_step, detail=detail or {}
    )


def from_dict(data: object) -> AnalysisState:
    """Read back a state that crossed a JSON boundary.

    Results are handed between commands as dicts, and a consumer that wants to
    fold one into its own summary had to compare strings to do it. An
    unrecognised or absent state reads as "not evaluated" rather than as
    "checked", so a shape this does not understand cannot pass for a clean
    result.
    """
    if not isinstance(data, dict):
        return not_evaluated("no analysis state was reported")
    state = str(data.get("state") or "")
    if state not in STATES:
        return not_evaluated(f"unrecognised analysis state: {state or '(none)'}")
    reason = str(data.get("reason") or "")
    next_step = str(data.get("next_step") or "")
    stage = str(data.get("stage") or "")
    detail = data.get("detail")
    if state == CHECKED:
        return checked(detail if isinstance(detail, dict) else None)
    if state == NO_MEASUREMENT:
        return no_measurement(
            reason, next_step or "capture the value this answer depends on",
            detail if isinstance(detail, dict) else None,
        )
    return AnalysisState(
        state,
        reason=reason,
        next_step=next_step,
        stage=stage if stage in STAGES else DISCOVERY,
        detail=detail if isinstance(detail, dict) else {},
    )


def worst(states: list[AnalysisState]) -> AnalysisState:
    """The state a set of results should be reported under.

    One check that could not run makes the set inconclusive, however many
    others were clean: a summary that hides it is the failure this exists to
    stop.
    """
    order = {CHECKED: 0, NO_MEASUREMENT: 1, TRUNCATED: 2, PARTIAL: 3, UNSUPPORTED: 4, NOT_EVALUATED: 5}
    if not states:
        return AnalysisState(
            NOT_EVALUATED, reason="nothing was examined", stage=DISCOVERY
        )
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
