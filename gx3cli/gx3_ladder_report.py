from __future__ import annotations

"""A ladder you can open in a browser, offline, and click through.

The SVG export draws rungs and the cross-reference knows where every device is
read and written, and putting the two together has been left to the reader: a
picture in one window, a list of positions in another, and the work of matching
"st4377" in one to "st4377" in the other done by eye. That is where a reading
goes wrong quietly.

So: one HTML file, no network, three panes. The rungs down the middle, the
devices in this program on the left, and on the right -- for whichever device
was clicked -- where it is read, where it is written, and what its comment
says. Clicking an occurrence in this report scrolls to that rung.

Two things this file refuses to do.

It does not colour a contact as though it were on. Everything here comes from a
saved file; whether a contact is closed right now is not in it, and a picture
that looks like a monitor screen would be read as one. Where a condition
depends on something outside the ladder, it says so and names the device to go
and look at.

It does not present its own extent as the project's. A report of one program
that shows two writers for a device, when the project has seven, has told a
lie by omission. Every device panel carries both counts.
"""

import argparse
import html
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gx3cli.gx3_analysis_state import (
    CHECKED,
    DECODE,
    PARTIAL,
    REACH,
    TRUNCATED,
    AnalysisState,
    label_for,
    stage_label,
)
from gx3cli.gx3_ladder_layout import (
    ProjectSources,
    build_layout_payload,
    layouts_to_svg,
    load_project_sources,
)
from gx3cli.gx3_input_identity import fingerprint, short
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root
from gx3cli.gx3_version import package_version
from gx3cli.gx3_xref import open_xref_db


DEFAULT_RUNG_LIMIT = 200


@dataclass
class Occurrence:
    device: str
    access: str
    role: str
    detail: str  # access_basis: why this counts as a read or a write
    comment: str
    pos: int
    step: str
    parse_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "access": self.access,
            "role": self.role,
            "detail": self.detail,
            "comment": self.comment,
            "pos": self.pos,
            "step": self.step,
            "parse_status": self.parse_status,
        }


@dataclass
class Report:
    root: Path
    program: str
    input_sha256: str
    rungs: list[dict[str, Any]] = field(default_factory=list)
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    state: AnalysisState = field(default_factory=lambda: AnalysisState(CHECKED))
    total_rungs: int = 0
    pages: dict[str, str] = field(default_factory=dict)


def occurrences_for(con: sqlite3.Connection, lddb: str, positions: list[int]) -> dict[int, list[Occurrence]]:
    """Every recorded occurrence in the rungs this report draws."""
    found: dict[int, list[Occurrence]] = {}
    if not positions:
        return found
    chunk = 500
    for start in range(0, len(positions), chunk):
        window = positions[start : start + chunk]
        marks = ",".join("?" for _ in window)
        rows = con.execute(
            "select device, access, role, access_basis, comment, pos, step, parse_status "
            f"from xref where lddb = ? and pos in ({marks}) order by pos, id",
            [lddb, *window],
        ).fetchall()
        for row in rows:
            occ = Occurrence(
                device=row["device"],
                access=row["access"] or "",
                role=row["role"] or "",
                detail=row["access_basis"] or "",
                comment=row["comment"] or "",
                pos=int(row["pos"]),
                step=str(row["step"] or ""),
                parse_status=row["parse_status"] or "",
            )
            found.setdefault(occ.pos, []).append(occ)
    return found


def project_occurrences(
    con: sqlite3.Connection, devices: list[str], limit_per_device: int = 200
) -> dict[str, list[dict[str, Any]]]:
    """Every place in the project each device is read or written.

    The counts alone told a reader that five writers existed somewhere else;
    they did not let them go and look. This is what "reach every write from the
    comment search" needs, and the page turns each entry into a link to the
    program that holds it.
    """
    found: dict[str, list[dict[str, Any]]] = {}
    chunk = 500
    for start in range(0, len(devices), chunk):
        window = devices[start : start + chunk]
        marks = ",".join("?" for _ in window)
        rows = con.execute(
            f"select device, access, role, access_basis, lddb, pos, pou, step "
            f"from xref where device in ({marks}) order by device, pou, pos",
            window,
        ).fetchall()
        for row in rows:
            entries = found.setdefault(str(row["device"]), [])
            if len(entries) >= limit_per_device:
                continue
            entries.append(
                {
                    "access": row["access"] or "",
                    "role": row["role"] or "",
                    "basis": row["access_basis"] or "",
                    "lddb": str(row["lddb"] or ""),
                    "pos": int(row["pos"] or 0),
                    "pou": str(row["pou"] or ""),
                    "step": row["step"],
                }
            )
    return found


def project_totals(con: sqlite3.Connection, devices: list[str]) -> dict[str, dict[str, int]]:
    """How often each device is read and written in the *whole* project.

    The report covers one program. Without this, a device panel showing two
    writers reads as "this device has two writers", and the other five in
    another program are invisible.
    """
    totals: dict[str, dict[str, int]] = {}
    chunk = 500
    for start in range(0, len(devices), chunk):
        window = devices[start : start + chunk]
        marks = ",".join("?" for _ in window)
        rows = con.execute(
            f"select device, access, count(*) as n from xref where device in ({marks}) "
            "group by device, access",
            window,
        ).fetchall()
        for row in rows:
            entry = totals.setdefault(row["device"], {"read": 0, "write": 0})
            if row["access"] in entry:
                entry[row["access"]] += int(row["n"])
    return totals


def build(
    root: Path,
    xref_db: Path,
    program: str,
    device: str | None = None,
    limit: int = DEFAULT_RUNG_LIMIT,
    sources: ProjectSources | None = None,
) -> Report:
    payload = build_layout_payload(root, program, None, device, sources)
    lddb = str(payload["program"])
    all_rungs = list(payload["rungs"])
    total = len(all_rungs)
    kept = all_rungs[:limit] if limit > 0 else all_rungs

    con = open_xref_db(xref_db, read_only=True, root=root)
    try:
        positions = [int(r["pos"]) for r in kept if r.get("pos") is not None]
        by_pos = occurrences_for(con, lddb, positions)

        rungs: list[dict[str, Any]] = []
        partial = 0
        for layout in kept:
            pos = layout.get("pos")
            occurrences = by_pos.get(int(pos), []) if pos is not None else []
            if any(occ.parse_status and occ.parse_status != "exact" for occ in occurrences):
                partial += 1
            rungs.append(
                {
                    "pos": pos,
                    "step": layout.get("step"),
                    "title": layout.get("title") or "",
                    "svg": layouts_to_svg({"root": str(root), "program": lddb, "rungs": [layout]}),
                    "occurrences": [occ.as_dict() for occ in occurrences],
                }
            )

        names = sorted({occ["device"] for rung in rungs for occ in rung["occurrences"]})
        totals = project_totals(con, names)
        everywhere = project_occurrences(con, names)
    finally:
        con.close()

    devices: dict[str, dict[str, Any]] = {}
    for name in names:
        here = [
            {"pos": rung["pos"], "step": rung["step"], **occ}
            for rung in rungs
            for occ in rung["occurrences"]
            if occ["device"] == name
        ]
        comment = next((occ["comment"] for occ in here if occ["comment"]), "")
        devices[name] = {
            "device": name,
            "comment": comment,
            "here": here,
            "project": totals.get(name, {"read": 0, "write": 0}),
            "everywhere": everywhere.get(name, []),
        }

    if total > len(kept):
        state = AnalysisState(
            TRUNCATED,
            reason=f"{len(kept)} of {total} rungs are in this report",
            next_step="raise --limit, or narrow it with --device",
            stage=REACH,
        )
    elif partial:
        state = AnalysisState(
            PARTIAL,
            reason=f"{partial} rungs contain operands that were not fully interpreted",
            next_step="gx3-cli parse-gaps --root <project>",
            stage=DECODE,
        )
    else:
        state = AnalysisState(CHECKED)

    return Report(
        root=Path(root),
        program=lddb,
        input_sha256=fingerprint(Path(root)),
        rungs=rungs,
        devices=devices,
        state=state,
        total_rungs=total,
    )


STYLE = """
:root { color-scheme: light; --line:#d5d8dc; --ink:#1b1d1f; --muted:#5d6469;
        --write:#8a3b00; --read:#1f4e79; --warn:#7a5c00; --panel:#f7f8f9; }
* { box-sizing: border-box; }
body { margin:0; font:14px/1.55 "Segoe UI", "Yu Gothic UI", system-ui, sans-serif; color:var(--ink); }
header { padding:10px 16px; border-bottom:1px solid var(--line); background:var(--panel); }
header h1 { margin:0 0 4px; font-size:16px; font-weight:600; }
header .meta { color:var(--muted); font-size:12px; }
header .state { margin-top:6px; padding:6px 10px; border-left:3px solid var(--warn); background:#fffdf3; font-size:12px; }
main { display:grid; grid-template-columns: 260px minmax(0,1fr) 320px; height: calc(100vh - 78px); }
aside, section { overflow:auto; }
aside { border-right:1px solid var(--line); padding:10px; }
#right { border-right:0; border-left:1px solid var(--line); }
input[type=search] { width:100%; padding:6px 8px; border:1px solid var(--line); border-radius:4px; font:inherit; }
ul { list-style:none; margin:8px 0 0; padding:0; }
li { padding:4px 6px; border-radius:4px; cursor:pointer; }
li:hover { background:var(--panel); }
li.sel { background:#e8eef5; }
.dev { font-family: Consolas, "Courier New", monospace; font-weight:600; }
.cmt { color:var(--muted); font-size:12px; display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.cmt.open { white-space:normal; }
.nocmt { color:#8a8f94; font-style:italic; }
.rung { padding:12px 16px; border-bottom:1px solid var(--line); }
.rung.hit { background:#fdf8e6; }
.rung h2 { margin:0 0 6px; font-size:12px; color:var(--muted); font-weight:600; }
.rung svg { max-width:100%; height:auto; }
#right h2 { font-size:13px; margin:12px 0 4px; }
.occ { padding:4px 6px; border-radius:4px; cursor:pointer; font-size:13px; }
.occ:hover { background:var(--panel); }
.write { color:var(--write); } .read { color:var(--read); }
.counts { font-size:12px; color:var(--muted); margin:2px 0 8px; }
.note { font-size:12px; color:var(--muted); border-top:1px solid var(--line); margin-top:14px; padding-top:8px; }
.missing { margin:12px 16px; padding:8px 10px; border-left:3px solid var(--warn); background:#fffdf3; font-size:13px; }
nav.pages { padding:8px 16px; border-top:1px solid var(--line); font-size:12px; background:var(--panel); }
nav.pages a { margin-right:10px; }
button.toggle { font:inherit; font-size:12px; border:1px solid var(--line); background:#fff; border-radius:4px; cursor:pointer; padding:2px 8px; }
"""

SCRIPT = """
const data = window.__REPORT__;
const list = document.getElementById('devices');
const right = document.getElementById('right');
const search = document.getElementById('search');
let selected = null;

function commentHtml(text) {
  if (!text) return '<span class="nocmt">コメントなし / no comment</span>';
  return '<span class="cmt" title="' + text.replace(/"/g, '&quot;') + '">' + text + '</span>';
}

function renderList() {
  const q = (search.value || '').toLowerCase();
  const names = Object.keys(data.devices).filter(function (name) {
    if (!q) return true;
    const d = data.devices[name];
    return name.toLowerCase().indexOf(q) >= 0 || (d.comment || '').toLowerCase().indexOf(q) >= 0;
  });
  list.innerHTML = '';
  names.forEach(function (name) {
    const d = data.devices[name];
    const li = document.createElement('li');
    li.innerHTML = '<span class="dev">' + name + '</span>' + commentHtml(d.comment);
    li.onclick = function () { select(name); };
    if (name === selected) li.className = 'sel';
    list.appendChild(li);
  });
  document.getElementById('count').textContent = names.length + ' / ' + Object.keys(data.devices).length;
}

function select(name) {
  selected = name;
  const d = data.devices[name];
  const here = d.here;
  const writes = here.filter(function (o) { return o.access === 'write'; });
  const reads = here.filter(function (o) { return o.access === 'read'; });
  let html = '<h2>' + name + '</h2>' + commentHtml(d.comment);
  html += '<p class="counts">この画面: 書込 ' + writes.length + ' / 読出 ' + reads.length +
          '<br>プロジェクト全体: 書込 ' + d.project.write + ' / 読出 ' + d.project.read + '</p>';
  const elsewhere = (d.everywhere || []).filter(function (o) { return o.lddb !== data.program; });
  if (elsewhere.length) {
    html += '<h2>ほかのプログラム (' + elsewhere.length + ')</h2>';
    elsewhere.forEach(function (o) {
      const page = (data.pages || {})[o.lddb];
      const label = (o.access === 'write' ? '書込' : '読出') + ' ' + (o.pou || o.lddb) +
                    ' step ' + (o.step === null || o.step === undefined ? o.pos : o.step);
      if (page) {
        html += '<div class="occ ' + o.access + '"><a href="' + page + '#pos-' + o.pos + '">' +
                label + '</a> <span class="cmt">' + (o.basis || '') + '</span></div>';
      } else {
        html += '<div class="occ ' + o.access + '">' + label +
                ' <span class="cmt">（このページ群に含まれないプログラム）</span></div>';
      }
    });
  }
  ['write', 'read'].forEach(function (kind) {
    const rows = kind === 'write' ? writes : reads;
    html += '<h2>' + (kind === 'write' ? '書き込み' : '読み出し') + ' (' + rows.length + ')</h2>';
    rows.forEach(function (o) {
      html += '<div class="occ ' + kind + '" data-pos="' + o.pos + '">step ' + (o.step || o.pos) +
              '  ' + (o.role || '') + '  <span class="cmt">' + (o.detail || '') + '</span></div>';
    });
    if (!rows.length) html += '<div class="counts">なし</div>';
  });
  right.innerHTML = html + noteHtml();
  Array.prototype.forEach.call(right.querySelectorAll('.occ'), function (el) {
    el.onclick = function () { jump(el.getAttribute('data-pos')); };
  });
  renderList();
  highlight(name);
}

function noteHtml() {
  return '<p class="note">この画面はファイルから読んだ静的な条件です。' +
         '接点が「いま ON か」はファイルにありません。現場で確認する値は、' +
         '上の一覧のデバイスを実機で見てください。</p>';
}

function highlight(name) {
  Array.prototype.forEach.call(document.querySelectorAll('.rung'), function (el) {
    const has = (el.getAttribute('data-devices') || '').split(' ').indexOf(name) >= 0;
    el.className = has ? 'rung hit' : 'rung';
  });
}

function jump(pos) {
  const el = document.querySelector('.rung[data-pos="' + pos + '"]');
  if (el) el.scrollIntoView({ block: 'center' });
}

search.oninput = renderList;
renderList();

// A link from another page names a rung by position. This page may hold fewer
// rungs than its program does, so the rung it points at can be absent -- and a
// link that silently lands nowhere is worse than one that says so.
(function () {
  const hash = (location.hash || '').replace('#', '');
  if (!hash) return;
  const target = document.getElementById(hash);
  if (target) {
    target.scrollIntoView({ block: 'center' });
    target.className = 'rung hit';
    return;
  }
  const note = document.createElement('div');
  note.className = 'missing';
  note.textContent = 'リンク先のラング (' + hash.replace('pos-', 'pos ') +
    ') はこのページにありません。このページは ' + data.drawn + ' / ' + data.total +
    ' ラングを描いています。--limit を上げて作り直してください。';
  document.querySelector('#rungs').prepend(note);
})();
"""


def render_html(report: Report) -> str:
    def esc(text: object) -> str:
        return html.escape(str(text or ""), quote=True)

    rung_html: list[str] = []
    for rung in report.rungs:
        devices = " ".join(sorted({occ["device"] for occ in rung["occurrences"]}))
        head = f"step {rung['step']}" if rung.get("step") else f"pos {rung['pos']}"
        title = f" — {esc(rung['title'])}" if rung.get("title") else ""
        rung_html.append(
            f'<div class="rung" id="pos-{esc(rung["pos"])}" data-pos="{esc(rung["pos"])}"'
            f' data-devices="{esc(devices)}">'
            f"<h2>{esc(head)}{title}</h2>{rung['svg']}</div>"
        )

    state_html = ""
    if report.state.state != CHECKED:
        state_html = (
            f'<div class="state">{esc(label_for(report.state.state, ja=True))}'
            f"（{esc(stage_label(report.state.stage, ja=True))}）"
            f" — {esc(report.state.reason)}"
            + (f"<br>次の手順: {esc(report.state.next_step)}" if report.state.next_step else "")
            + "</div>"
        )

    page_links = ""
    if report.pages:
        links = " ".join(
            f'<a href="{esc(file_name)}">{esc(name)}</a>'
            + (" (this one)" if name == report.program else "")
            for name, file_name in sorted(report.pages.items())
        )
        page_links = f"<strong>programs:</strong> {links}"

    payload = {
        "devices": report.devices,
        "program": report.program,
        "input_sha256": report.input_sha256,
        # Which programs were written beside this one, and under what file
        # name. An occurrence in one of them becomes a link; an occurrence in a
        # program that was not written stays plain text saying where it is,
        # rather than a link that goes nowhere.
        "pages": report.pages,
        "drawn": len(report.rungs),
        "total": report.total_rungs,
    }
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>{esc(report.program)} — ladder report</title>
<style>{STYLE}</style></head><body>
<header>
  <h1>{esc(report.program)}</h1>
  <div class="meta">
    {esc(report.root)} &nbsp;|&nbsp; 入力 {esc(short(report.input_sha256))}
    &nbsp;|&nbsp; {len(report.rungs)} / {report.total_rungs} rungs
    &nbsp;|&nbsp; {esc(package_version())}
  </div>
  {state_html}
</header>
<main>
  <aside>
    <input id="search" type="search" placeholder="デバイス / コメントで検索">
    <div class="counts" id="count"></div>
    <ul id="devices"></ul>
  </aside>
  <section id="rungs">{''.join(rung_html)}</section>
  <aside id="right"><p class="counts">左の一覧からデバイスを選んでください。</p></aside>
</main>
<nav class="pages">{page_links}</nav>
<script>window.__REPORT__ = {json.dumps(payload, ensure_ascii=False)};</script>
<script>{SCRIPT}</script>
</body></html>
"""


def page_name(lddb: str) -> str:
    stem = lddb[: -len("_LDDB.db")] if lddb.endswith("_LDDB.db") else lddb
    return f"{stem}.html"


def build_set(
    root: Path, xref_db: Path, programs: list[str], out_dir: Path, limit: int
) -> list[Report]:
    """One page per program, each linking to the others.

    A single page for a whole project is the obvious idea and the wrong one:
    the one program tried here came to 260KB of embedded SVG for 86 rungs, so
    seventy of them is not a page anybody opens. A page each, and a link
    between them, is what makes "reach every write" true without making any one
    file unusable.
    """
    pages = {name: page_name(name) for name in programs}
    reports: list[Report] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = load_project_sources(root)
    for name in programs:
        report = build(root, xref_db, name, None, limit, sources)
        report.pages = pages
        (out_dir / pages[report.program]).write_text(render_html(report), encoding="utf-8")
        reports.append(report)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a single offline HTML report: rungs, devices, and where each is read and written.",
    )
    parser.add_argument(
        "program", nargs="?", default="",
        help="program name (POU / program file) or LDDB file name; omit with --all",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="one page per program, linked to each other, so a write in another program can be reached",
    )
    parser.add_argument("--out-dir", default="", help="where --all writes its pages")
    parser.add_argument("--root", default=str(default_project_root()))
    parser.add_argument("--xref-db", help="cross-reference DB; defaults to the one for this project")
    parser.add_argument("--device", help="only rungs that reference this device")
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_RUNG_LIMIT,
        help="how many rungs to draw; 0 for all. A report says when it holds fewer than the program does",
    )
    parser.add_argument("-o", "--output", default="", help="where to write the HTML")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = resolve_project_root(args.root)
    if args.xref_db:
        xref_db = Path(args.xref_db)
    else:
        from gx3cli.gx3_workspace import prepare

        xref_db = prepare(root).xref.path

    if args.all:
        programs = sorted(path.name for path in root.glob("*_LDDB.db"))
        if not programs:
            print(f"no ladder programs in {root}")
            return 1
        out_dir = Path(args.out_dir) if args.out_dir else Path("ladder_report")
        reports = build_set(root, xref_db, programs, out_dir, args.limit)
        drawn = sum(len(report.rungs) for report in reports)
        total = sum(report.total_rungs for report in reports)
        print(f"written: {out_dir}  ({len(reports)} programs, {drawn} / {total} rungs)")
        short_pages = [report for report in reports if report.state.state != CHECKED]
        for report in short_pages[:10]:
            print(f"  {report.program}: {report.state.line('', ja=True)}")
        if len(short_pages) > 10:
            print(f"  ... {len(short_pages) - 10} more pages carry a note")
        return 0

    if not args.program:
        print("name a program, or use --all")
        return 1
    report = build(root, xref_db, args.program, args.device, args.limit)
    out = Path(args.output) if args.output else Path(f"{report.program}_report.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(report), encoding="utf-8")
    print(f"written: {out}  ({len(report.rungs)} rungs, {len(report.devices)} devices)")
    if report.state.state != CHECKED:
        print(report.state.line("report", ja=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
