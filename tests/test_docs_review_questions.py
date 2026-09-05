from __future__ import annotations

"""The review questions have to be reachable, and their commands have to run.

A checklist nobody is pointed at is a file, not a practice, so the two places a
change passes through -- AGENTS.md for an agent, CONTRIBUTING.md for a person --
have to name it.

And the commands it ships have to work. A document that tells the reader to run
something that errors is the failure it was written about: it looks like a
procedure and is not one.

What is deliberately not tested here is the wording. The questions will be
rewritten as new kinds of bug turn up, and a test that pins their text would
make that harder rather than safer.
"""

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "REVIEW_QUESTIONS_JA.md"


def test_the_questions_exist_where_a_change_passes_through() -> None:
    assert DOC.exists(), DOC
    for entry in ("AGENTS.md", "CONTRIBUTING.md"):
        text = (ROOT / entry).read_text(encoding="utf-8")
        assert "REVIEW_QUESTIONS_JA.md" in text, f"{entry} does not point at the questions"


def test_every_shipped_command_runs() -> None:
    blocks = re.findall(r"```bash\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    assert blocks, "the questions ship no runnable command"
    for index, block in enumerate(blocks, 1):
        if not block.lstrip().startswith("python"):
            continue  # a shell one-liner; the python ones are the checks
        source = block.split("<<'PY'", 1)[1].rsplit("PY", 1)[0]
        completed = subprocess.run(
            [sys.executable, "-c", source],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        assert completed.returncode == 0, (
            f"command {index} in REVIEW_QUESTIONS_JA.md fails:\n{completed.stderr}"
        )


def test_each_question_carries_the_bug_it_found() -> None:
    # The rule the document sets for itself: only questions that actually found
    # something. A question with no example is one somebody thought sounded
    # good, and a checklist of those stops being read.
    text = DOC.read_text(encoding="utf-8")
    sections = re.split(r"\n## ", text)
    numbered = [s for s in sections if re.match(r"\d+\. ", s)]
    assert len(numbered) >= 8, len(numbered)
    for section in numbered:
        title = section.splitlines()[0]
        assert "実例" in section, f"no worked example under: {title}"


def main() -> int:
    test_the_questions_exist_where_a_change_passes_through()
    test_every_shipped_command_runs()
    test_each_question_carries_the_bug_it_found()
    print("review question checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
