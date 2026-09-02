from __future__ import annotations

"""One way to ask a command for its output, across all of them.

Sixty-odd commands grew their own answer to "how do I get this as JSON". Twenty
took `--json` as a flag, eleven took `--format json`, one took both, and
thirty-five had no way at all. Which one a command wanted was something you
found out by reading its `--help`, or by it failing.

That is a small thing on its own and a large one across sixty commands, because
the caller that suffers most is the one that cannot read help text: an agent
driving the CLI has to know per command which spelling to use.

So: `--format` is the spelling, `--json` stays as a shorthand where it already
existed, and both land in the same place. Commands opt in by calling
add_format_argument() and emit(); nothing is rewritten behind a command's back.

    add_format_argument(parser)                 # --format {text,json}
    ...
    return emit(args, text=lambda: lines, data=lambda: payload)

The renderers are passed as callables rather than as values so that a command
does not build the output it was not asked for -- some of them are expensive.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

TEXT = "text"
JSON = "json"
MARKDOWN = "markdown"
CSV = "csv"

DEFAULT_CHOICES = (TEXT, JSON)


def add_format_argument(
    parser: argparse.ArgumentParser,
    choices: Sequence[str] = DEFAULT_CHOICES,
    default: str = TEXT,
    json_shorthand: bool = True,
    output_argument: bool = True,
) -> None:
    """Give a command the shared output options.

    json_shorthand keeps `--json` working for the commands that already took
    it. It is not added to new ones: two spellings for one thing is what this
    module exists to stop, and the shorthand only survives because removing it
    would break callers.
    """
    parser.add_argument(
        "--format",
        choices=list(choices),
        default=default,
        help=f"output format (default: {default})",
    )
    if json_shorthand and JSON in choices:
        parser.add_argument(
            "--json",
            action="store_true",
            help="shorthand for --format json",
        )
    if output_argument:
        parser.add_argument(
            "-o",
            "--output",
            default="",
            help="write to this file instead of stdout",
        )


def add_format_alias(parser: argparse.ArgumentParser) -> None:
    """Let a command that already takes --json also take --format json.

    For the seventeen commands whose output is a flag, not a format. They keep
    reading args.json; fold_format_alias() sets it when --format json was used,
    so a caller can spell it the one way everywhere without those commands
    having to be rewritten.
    """
    parser.add_argument(
        "--format",
        choices=[TEXT, JSON],
        default=None,
        help="output format; --json is the same as --format json",
    )


def fold_format_alias(args: argparse.Namespace) -> argparse.Namespace:
    """Apply --format to args.json. Call once, right after parse_args()."""
    if getattr(args, "format", None) == JSON:
        args.json = True
    return args


def chosen_format(args: argparse.Namespace, default: str = TEXT) -> str:
    """What the caller asked for, with --json folded in."""
    if getattr(args, "json", False):
        return JSON
    return str(getattr(args, "format", default) or default)


def render_json(data: Any) -> str:
    """The one JSON spelling: UTF-8 kept as-is, two-space indent."""
    return json.dumps(data, ensure_ascii=False, indent=2)


def render_text(text: str | Iterable[str]) -> str:
    if isinstance(text, str):
        return text
    return "\n".join(str(line) for line in text)


def emit(
    args: argparse.Namespace,
    text: Callable[[], str | Iterable[str]] | None = None,
    data: Callable[[], Any] | None = None,
    markdown: Callable[[], str | Iterable[str]] | None = None,
    csv_text: Callable[[], str | Iterable[str]] | None = None,
    default: str = TEXT,
) -> int:
    """Render in the asked-for format and write it where it was asked for.

    Returns the exit code, so a command body can end with `return emit(...)`.
    """
    fmt = chosen_format(args, default)
    renderers: dict[str, Callable[[], str | Iterable[str]] | None] = {
        TEXT: text,
        MARKDOWN: markdown,
        CSV: csv_text,
    }
    if fmt == JSON:
        if data is None:
            print(f"this command has no {JSON} output")
            return 2
        body = render_json(data())
    else:
        renderer = renderers.get(fmt)
        if renderer is None:
            print(f"this command has no {fmt} output")
            return 2
        body = render_text(renderer())

    destination = getattr(args, "output", "")
    if destination:
        path = Path(destination)
        if path.parent != Path(""):
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n", encoding="utf-8")
        print(f"wrote: {path}")
    else:
        print(body)
    return 0
