from __future__ import annotations

"""The report has to say what it is not showing.

Two ways a page like this misleads without saying anything false. It covers one
program, so a device with two writers here and five elsewhere looks like a
device with two writers -- every device panel therefore carries the project
totals beside its own. And it draws a fixed number of rungs, so a program
longer than that looks like a program that ends there -- the page says so in
the same words the other commands use for a search that stopped at a limit.

The third thing pinned here is a refusal: nothing in the page states or colours
a contact as though its current value were known. Everything in it was read
from a saved file.
"""

import re
import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_analysis_state import CHECKED, TRUNCATED
from gx3cli.gx3_ladder_report import build, render_html
from gx3cli.gx3_synthetic_project import create_demo_line_project
from gx3cli.gx3_workspace import prepare


def a_project(tmp: str) -> tuple[Path, Path, str]:
    project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
    workspace = prepare(project)
    program = next(project.glob("*_LDDB.db")).name
    return project, workspace.xref.path, program


def test_a_report_carries_its_rungs_and_the_devices_in_them() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project, xref, program = a_project(tmp)
        report = build(project, xref, program, limit=0)
        assert report.rungs, "no rungs in the report"
        assert report.devices, "no devices in the report"
        for rung in report.rungs:
            assert rung["svg"].lstrip().startswith("<svg"), rung["svg"][:60]
        assert report.input_sha256


def test_every_device_panel_carries_the_project_totals_too() -> None:
    # A report of one program showing two writers, when the project has seven,
    # has told a lie by omission.
    with tempfile.TemporaryDirectory() as tmp:
        project, xref, program = a_project(tmp)
        report = build(project, xref, program, limit=0)
        con = sqlite3.connect(xref)
        for name, entry in report.devices.items():
            assert "project" in entry, name
            row = con.execute(
                "select count(*) from xref where device = ? and access = 'write'", (name,)
            ).fetchone()
            assert entry["project"]["write"] == row[0], (name, entry["project"], row[0])
        con.close()


def test_a_report_that_holds_fewer_rungs_than_the_program_says_so() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project, xref, program = a_project(tmp)
        whole = build(project, xref, program, limit=0)
        if whole.total_rungs < 2:
            return  # nothing to truncate; the fixture is too small to test with
        part = build(project, xref, program, limit=1)
        assert part.state.state == TRUNCATED, part.state
        assert str(whole.total_rungs) in part.state.reason, part.state.reason
        assert not part.state.conclusive
        page = render_html(part)
        assert "探索打切り" in page, "the page does not say it is partial"


def test_a_whole_report_makes_no_such_claim() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project, xref, program = a_project(tmp)
        report = build(project, xref, program, limit=0)
        if report.state.state == CHECKED:
            assert "探索打切り" not in render_html(report)


def test_the_page_never_claims_to_know_a_present_value() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project, xref, program = a_project(tmp)
        page = render_html(build(project, xref, program, limit=0))
        for forbidden in ("現在ON", "現在OFF", "currently on", "live value", "monitor"):
            assert forbidden.lower() not in page.lower(), forbidden
        assert "ファイルから読んだ静的な条件" in page


def test_the_page_is_one_file_that_opens_without_a_network() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project, xref, program = a_project(tmp)
        page = render_html(build(project, xref, program, limit=0))
        # No stylesheet, script or image fetched from anywhere.
        assert not re.search(r"""src\s*=\s*["']https?://""", page), "the page loads something remote"
        assert not re.search(r"""href\s*=\s*["']https?://""", page), "the page loads something remote"
        assert "<script>" in page and "</script>" in page


def test_a_device_without_a_comment_is_said_to_have_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project, xref, program = a_project(tmp)
        report = build(project, xref, program, limit=0)
        page = render_html(report)
        if any(not entry["comment"] for entry in report.devices.values()):
            assert "コメントなし" in page


def test_a_write_in_another_program_can_be_reached() -> None:
    """The completion condition #49 sets: reach every write, not a count of them.

    A page covering one program used to say "seven writers in the project" and
    leave the reader to find the other five. Each of those is now a link into
    the page for the program that holds it.
    """
    import re

    from gx3cli.gx3_ladder_report import build_set, page_name

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project, xref, _ = a_project(tmp)
        programs = sorted(path.name for path in project.glob("*_LDDB.db"))
        if len(programs) < 2:
            return  # a single-program fixture has no other program to reach

        out = work / "pages"
        reports = build_set(project, xref, programs, out, limit=0)
        assert len(reports) == len(programs)
        for name in programs:
            assert (out / page_name(name)).exists(), name

        # Some device on some page is used in another program, and the entry
        # for it points at a page that exists, at a rung that exists.
        checked = 0
        for report in reports:
            page = (out / page_name(report.program)).read_text(encoding="utf-8")
            for device in report.devices.values():
                for occurrence in device["everywhere"]:
                    if occurrence["lddb"] == report.program:
                        continue
                    target = report.pages.get(occurrence["lddb"])
                    assert target, (report.program, occurrence)
                    other = (out / target).read_text(encoding="utf-8")
                    assert f'id="pos-{occurrence["pos"]}"' in other, occurrence
                    checked += 1
                    if checked >= 5:
                        return
        assert checked, "no device was used in more than one program"


def test_a_link_into_a_shortened_page_is_not_left_silent() -> None:
    # Pages hold a bounded number of rungs, so a link can name one that is not
    # drawn. Landing nowhere with no explanation reads as a broken report.
    with tempfile.TemporaryDirectory() as tmp:
        project, xref, program = a_project(tmp)
        page = render_html(build(project, xref, program, limit=1))
        assert "このページにありません" in page, "no handling for a link that misses"


def test_every_program_page_lists_the_others() -> None:
    from gx3cli.gx3_ladder_report import build_set, page_name

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project, xref, _ = a_project(tmp)
        programs = sorted(path.name for path in project.glob("*_LDDB.db"))
        out = work / "pages"
        build_set(project, xref, programs, out, limit=1)
        for name in programs:
            page = (out / page_name(name)).read_text(encoding="utf-8")
            for other in programs:
                assert page_name(other) in page, (name, other)


def main() -> int:
    test_a_report_carries_its_rungs_and_the_devices_in_them()
    test_every_device_panel_carries_the_project_totals_too()
    test_a_report_that_holds_fewer_rungs_than_the_program_says_so()
    test_a_whole_report_makes_no_such_claim()
    test_the_page_never_claims_to_know_a_present_value()
    test_the_page_is_one_file_that_opens_without_a_network()
    test_a_device_without_a_comment_is_said_to_have_none()
    test_a_write_in_another_program_can_be_reached()
    test_a_link_into_a_shortened_page_is_not_left_silent()
    test_every_program_page_lists_the_others()
    print("ladder report checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
