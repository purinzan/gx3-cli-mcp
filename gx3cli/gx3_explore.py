from __future__ import annotations

"""Four questions, instead of sixty commands.

`list` names sixty-odd commands and `guide` narrows them to the ones this
project has evidence for, and both still leave the reader deciding what to run
first, then what to run after that, then which of the three databases the next
one wants. Someone who came to find out why a cylinder does not extend has to
learn a tool before they can ask.

So there are four entry points, one per question people actually arrive with:

    overview   what is in this project
    why        what makes this device turn on, and what holds it off
    concerns   what looks wrong in here
    changed    what is different from that other version

Each is a route through the commands that already exist. Nothing here analyses
a ladder: it prepares the index if it is missing, runs the existing commands in
an order that makes sense for the question, and prints them under one heading
with the input they were all read from. Analysis that lives in two places
disagrees with itself eventually, and the disagreement is invisible.

The header is not decoration. Every one of these answers is about a particular
input, and the fingerprint above them is what says the sections were read from
the same one.
"""

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gx3cli.gx3_cli import cli_argv, python_env
from gx3cli.gx3_input_identity import short
from gx3cli.gx3_project_paths import (
    LEGACY_OUTPUT_PREFIX_ENV,
    OUTPUT_PREFIX_ENV,
    default_project_root,
)
from gx3cli.gx3_workspace import Workspace, locate, prepare


@dataclass
class Context:
    root: Path
    workspace: Workspace
    ja: bool = False
    target: str = ""
    against: str = ""
    max_depth: int = 3
    timeout: float = 120.0

    @property
    def side_files(self) -> Path:
        """Where a step's CSVs and reports land.

        Several of these commands write files beside their output, relative to
        the working directory. Run them from the package directory and they
        fill gx3cli/outputs with one project's data and leave it there; run
        them from the caller's directory and they scatter CSVs wherever the
        person happened to be standing. Beside the index is neither: it belongs
        to this project and it is somewhere findable.
        """
        return self.workspace.directory / "explore_outputs"


# A step returns the argv to run, or None when the question does not reach it.
Argv = Callable[[Context], list[str] | None]


@dataclass(frozen=True)
class Step:
    name: str
    title_en: str
    title_ja: str
    argv: Argv
    # An optional step failing is reported and does not fail the run: a project
    # with no communication units has no network to map, and that is not an
    # error in the answer to "what is in this project".
    optional: bool = False

    def title(self, ja: bool) -> str:
        return self.title_ja if ja else self.title_en


def _overview_steps() -> tuple[Step, ...]:
    return (
        Step(
            "project-config", "CPU, units, addresses", "CPU・ユニット・アドレス",
            lambda c: ["project-config", "--root", str(c.root)],
        ),
        Step(
            "exec-config", "programs and execution order", "プログラムと実行順",
            lambda c: ["exec-config", "--root", str(c.root), "--db", str(c.workspace.xref.path)],
        ),
        Step(
            "metrics", "where the logic is", "ロジックの分布",
            lambda c: ["metrics", "--root", str(c.root), "--xref-db", str(c.workspace.xref.path)],
        ),
        Step(
            "reliability-report", "what was not read", "読めなかった範囲",
            lambda c: ["reliability-report", "--root", str(c.root)],
        ),
    )


def _why_steps() -> tuple[Step, ...]:
    def trace(c: Context) -> list[str] | None:
        args = [
            "trace-device", c.target, "--root", str(c.root),
            "--strict-logic", "--compact", "--max-depth", str(c.max_depth),
        ]
        return [*args, "--ja"] if c.ja else args

    return (
        Step(
            "trace-device", "what makes it turn on", "何が成立させるか",
            trace,
        ),
        Step(
            "where-used", "every rung that reads or writes it", "読み書きしている全ラング",
            lambda c: [
                "xref", "--root", str(c.root), "--db", str(c.workspace.xref.path),
                "where-used", c.target,
            ],
        ),
    )


def _concerns_steps() -> tuple[Step, ...]:
    return (
        Step(
            "lint", "static checks over the rungs", "ラダーの静的検査",
            lambda c: [
                "lint", str(c.root),
                "--xref-db", str(c.workspace.xref.path),
                "--index-db", str(c.workspace.index.path),
            ],
        ),
        Step(
            "dead-logic", "rungs that can never become true", "成立しえないラング",
            lambda c: ["dead-logic", "--root", str(c.root), "--db", str(c.workspace.xref.path)],
            optional=True,
        ),
    )


def _changed_steps() -> tuple[Step, ...]:
    return (
        Step(
            "semantic-diff", "what is different, rung by rung", "ラング単位の差分",
            lambda c: ["semantic-diff", str(c.against), str(c.root)],
        ),
    )


@dataclass(frozen=True)
class Purpose:
    name: str
    question_en: str
    question_ja: str
    steps: tuple[Step, ...]
    needs_target: bool = False
    needs_against: bool = False

    def question(self, ja: bool) -> str:
        return self.question_ja if ja else self.question_en


PURPOSES: tuple[Purpose, ...] = (
    Purpose(
        "overview",
        "what is in this project",
        "このプロジェクトに何があるか",
        _overview_steps(),
    ),
    Purpose(
        "why",
        "what makes this device turn on, and what holds it off",
        "このデバイスは何で成立し、何で止まるか",
        _why_steps(),
        needs_target=True,
    ),
    Purpose(
        "concerns",
        "what looks wrong in here",
        "気になる箇所はどこか",
        _concerns_steps(),
    ),
    Purpose(
        "changed",
        "what is different from that other version",
        "別の版と何が違うか",
        _changed_steps(),
        needs_against=True,
    ),
)

BY_NAME = {purpose.name: purpose for purpose in PURPOSES}


TIMED_OUT = -9


def run_step(step: Step, context: Context) -> tuple[int, str]:
    """Run one step, and stop waiting rather than hang the whole answer.

    `metrics` takes over two minutes on a real project of this size. An entry
    point that sits there is one a person kills, and then has none of the other
    sections either. A step that ran out of time is reported as a section that
    did not finish -- which is not the same as a section that found nothing.
    """
    argv = step.argv(context)
    if argv is None:
        return 0, ""
    context.side_files.mkdir(parents=True, exist_ok=True)
    env = python_env(str(context.root))
    env[OUTPUT_PREFIX_ENV] = str(context.side_files / "project")
    env[LEGACY_OUTPUT_PREFIX_ENV] = env[OUTPUT_PREFIX_ENV]
    try:
        completed = subprocess.run(
            cli_argv(argv),
            cwd=context.side_files,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=context.timeout if context.timeout > 0 else None,
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        return TIMED_OUT, expired.stdout or ""
    return completed.returncode, completed.stdout


def header(purpose: Purpose, context: Context) -> list[str]:
    ja = context.ja
    workspace = context.workspace
    lines = [
        f"{'問い' if ja else 'question'}: {purpose.question(ja)}",
        f"{'対象' if ja else 'project'}:  {context.root}",
        f"{'入力' if ja else 'input'}:    {short(workspace.input_sha256)}",
    ]
    if context.target:
        lines.append(f"{'デバイス' if ja else 'device'}:   {context.target}")
    if context.against:
        lines.append(f"{'比較先' if ja else 'against'}:  {context.against}")
    if workspace.built:
        built = "、".join(workspace.built) if ja else ", ".join(workspace.built)
        lines.append(f"{'索引' if ja else 'index'}:    " + (f"作成: {built}" if ja else f"built: {built}"))
    elif workspace.reused:
        lines.append(f"{'索引' if ja else 'index'}:    " + ("既存を再利用" if ja else "reused"))
    lines.append("")
    # Every section below was read from the input named above. Saying it once,
    # here, is what makes reading two sections side by side legitimate.
    return lines


def explore(purpose: Purpose, context: Context, stream=sys.stdout) -> int:
    for line in header(purpose, context):
        print(line, file=stream)

    failures: list[str] = []
    for step in purpose.steps:
        print(f"== {step.title(context.ja)}  ({step.name})", file=stream)
        code, output = run_step(step, context)
        text = output.strip()
        if text:
            print(text, file=stream)
        if code != 0:
            if code == TIMED_OUT:
                note = (
                    f"{context.timeout:.0f} 秒以内に終わりませんでした（探索打切り）。"
                    f"個別に実行してください: gx3-cli {step.name}"
                    if context.ja
                    else f"did not finish within {context.timeout:.0f}s; run it on its own: gx3-cli {step.name}"
                )
            else:
                note = (
                    "この項目は取得できませんでした"
                    if context.ja
                    else "this section could not be produced"
                )
            optional = "（任意）" if context.ja else " (optional)"
            print(f"-- {note}{optional if step.optional else ''}", file=stream)
            if not step.optional:
                failures.append(step.name)
        print("", file=stream)

    if context.side_files.is_dir() and any(context.side_files.iterdir()):
        head = "副産物" if context.ja else "files written"
        print(f"{head}: {context.side_files}", file=stream)
        print("", file=stream)

    if failures:
        head = "取得できなかった項目" if context.ja else "sections not produced"
        print(f"{head}: {', '.join(failures)}", file=stream)
        # A missing section is not a zero result. The reader is told which
        # question went unanswered rather than reading the rest as complete.
        return 1
    return 0


def build_context(args: argparse.Namespace, purpose: Purpose) -> Context:
    # Steps run with cwd set to the package directory, so a relative root --
    # "../_extracted_line", which is what a person types -- resolves somewhere
    # else there and every section reports an empty project.
    root = Path(args.root).resolve()
    if args.no_prepare:
        workspace = locate(root)
    else:
        workspace = prepare(root, rebuild=args.rebuild, quiet=True)
    return Context(
        root=root,
        workspace=workspace,
        ja=args.ja,
        target=getattr(args, "device", "") or "",
        against=str(Path(args.against).resolve()) if getattr(args, "against", "") else "",
        max_depth=args.max_depth,
        timeout=args.step_timeout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Answer one of four questions about a project, preparing whatever it needs.",
    )
    parser.add_argument(
        "purpose",
        choices=[p.name for p in PURPOSES],
        help="overview | why <device> | concerns | changed --against <project>",
    )
    parser.add_argument("device", nargs="?", default="", help="for `why`: the device to explain")
    parser.add_argument("--root", default=str(default_project_root()))
    parser.add_argument("--against", default="", help="for `changed`: the other project")
    parser.add_argument("--ja", action="store_true", help="Japanese headings")
    parser.add_argument("--max-depth", type=int, default=3, help="for `why`: how far upstream to look")
    parser.add_argument(
        "--step-timeout", type=float, default=120.0,
        help="seconds to give each section before moving on; 0 waits indefinitely",
    )
    parser.add_argument("--no-prepare", action="store_true", help="do not build a missing index")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the index before answering")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    purpose = BY_NAME[args.purpose]
    root = Path(args.root)
    if not root.is_dir():
        print(f"not a project directory: {root}")
        return 1
    if purpose.needs_target and not args.device:
        print(f"`{purpose.name}` needs a device: gx3-cli explore why M1234 --root {root}")
        return 1
    if purpose.needs_against and not args.against:
        print(f"`{purpose.name}` needs the other version: gx3-cli explore changed --against <project>")
        return 1

    context = build_context(args, purpose)
    if not context.workspace.ready and not args.no_prepare:
        print("the index could not be prepared; run: gx3-cli workspace --prepare")
        return 1
    return explore(purpose, context)


if __name__ == "__main__":
    raise SystemExit(main())
