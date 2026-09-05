from __future__ import annotations

"""Asking the cross-reference about a device, without having to know the rule.

`xref.device` looks like a key and is not. A row with `range_len > 1` is one
occurrence covering a run: `BMOV D300 D400 K4` is stored once, under D400, and
D401 through D403 have no row at all. So `where device = ?` about the middle of
a run finds nothing, which reads as "nothing writes this device".

The correct predicate has been in gx3_xref.py all along. What did not exist was
a way to be right without knowing it, and half the readers wrote the plain
lookup: ladder-report showed zero writers for a device a BMOV fills, scan-order
could not find a stale read inside any run, alarm-map and timing-chart do the
same in their own queries.

So this is the boundary. Every reader asks here, the join to `xref_members`
happens once, and a reader that never heard of a run gets the right answer.

Two things it deliberately keeps apart, because collapsing them is its own kind
of wrong answer:

    the device an instruction names      xref.device
    the devices that occurrence covers   member_device, run_offset

`run_offset = 0` is the named occurrence. A caller showing the ladder wants
that one -- the rung says "BMOV D300 D400 K4", not "BMOV D300 D402 K4". A
caller asking who writes D402 wants the covering row. Four covered devices are
not four occurrences, and nothing here reports them as such.
"""

import sqlite3
from typing import Any, Iterable


NAMED_ONLY = "named"
COVERED = "covered"


def has_members(con: sqlite3.Connection) -> bool:
    """Whether this database carries the member index.

    A cross-reference built before it exists can still be read; the caller is
    told, rather than silently served the narrower answer.
    """
    row = con.execute(
        "select count(*) from sqlite_master where type='table' and name='xref_members'"
    ).fetchone()
    return bool(row and row[0])


def device_match(con: sqlite3.Connection) -> tuple[str, str]:
    """The FROM and the device predicate to use in a query of your own.

    Some readers need columns and grouping this module should not try to
    anticipate. They still must not spell the lookup themselves: a raw join to
    `xref_members` breaks on a cross-reference built before that table existed,
    which is how the first attempt at this fix crashed four commands on an old
    database.

    Returns (from_clause, where_fragment). The fragment takes one parameter,
    the device, and the row alias is always `x`.
    """
    if has_members(con):
        return (
            "xref x join xref_members m on m.src_id = x.id",
            "m.member_device = ?",
        )
    return ("xref x", "x.device = ?")


def occurrences_of(
    con: sqlite3.Connection,
    device: str,
    *,
    access: Iterable[str] | None = None,
    scope: str = COVERED,
    limit: int | None = None,
    columns: str = "x.*",
) -> list[sqlite3.Row]:
    """Every occurrence that touches this device.

    scope=COVERED includes the runs that cover it without naming it, which is
    what "who writes D402" means. scope=NAMED_ONLY restricts to occurrences the
    instruction spells, which is what a rung listing wants.
    """
    params: list[Any] = [device]
    if scope == NAMED_ONLY or not has_members(con):
        where = ["x.device = ?"]
        source = "xref x"
    else:
        where = ["m.member_device = ?"]
        source = "xref x join xref_members m on m.src_id = x.id"

    if access:
        access = tuple(access)
        where.append(f"x.access in ({','.join('?' * len(access))})")
        params.extend(access)

    sql = f"select {columns} from {source} where {' and '.join(where)} order by x.pou, x.pos, x.id"
    if limit is not None and limit >= 0:
        sql += " limit ?"
        params.append(limit)
    return con.execute(sql, params).fetchall()


def counts_for(con: sqlite3.Connection, devices: Iterable[str]) -> dict[str, dict[str, int]]:
    """Reads and writes per device, counting the runs that cover each one."""
    names = list(devices)
    totals: dict[str, dict[str, int]] = {name: {"read": 0, "write": 0} for name in names}
    if not names:
        return totals
    member = has_members(con)
    chunk = 500
    for start in range(0, len(names), chunk):
        window = names[start : start + chunk]
        marks = ",".join("?" for _ in window)
        if member:
            sql = (
                f"select m.member_device as device, x.access as access, count(*) as n "
                f"from xref x join xref_members m on m.src_id = x.id "
                f"where m.member_device in ({marks}) group by m.member_device, x.access"
            )
        else:
            sql = (
                f"select x.device as device, x.access as access, count(*) as n "
                f"from xref x where x.device in ({marks}) group by x.device, x.access"
            )
        for row in con.execute(sql, window):
            entry = totals.setdefault(str(row["device"]), {"read": 0, "write": 0})
            if row["access"] in entry:
                entry[row["access"]] += int(row["n"])
    return totals


def members_of(con: sqlite3.Connection, src_id: int) -> list[str]:
    """The devices one occurrence covers, in order."""
    if not has_members(con):
        return []
    return [
        str(row["member_device"])
        for row in con.execute(
            "select member_device from xref_members where src_id = ? order by run_offset",
            (src_id,),
        )
    ]
