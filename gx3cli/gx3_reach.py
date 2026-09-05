from __future__ import annotations

"""What one device leads to, asked once and answered the same way everywhere.

`change-impact` and `xref downstream` both walk the same graph -- a device is
read by a rung, that rung writes something else, and so on -- and each carried
its own SQL for it. So the value-flow edges reached downstream and not
change-impact, the block-instruction spans reached neither, and the exact
limit-reporting fixed in one was absent from the other. Three separate
corrections for one question.

The walk is deliberately small and returns two things: the devices reached, and
the limits that actually cost something. A limit is reported only when it hid
a device -- reaching the last depth and finding nothing beyond it is a complete
answer, and saying "truncated" there trains a reader to ignore the word.

Nothing here decides whether a path runs. A device is reached because the saved
file connects it; scan order, execution conditions and interlocks are not
settled by any of this and are not claimed to be.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any


SAME_RUNG = "same-rung"


@dataclass
class Step:
    """One device, and why it is in the answer."""

    device: str
    comment: str
    source: str
    basis: str
    pou: str
    step: object
    depth: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "comment": self.comment,
            "from": self.source,
            "basis": self.basis,
            "pou": self.pou,
            "step": self.step,
            "depth": self.depth,
        }


@dataclass
class Reach:
    steps: list[Step] = field(default_factory=list)
    # "max-depth", "max-nodes" -- only when one of them hid something.
    stopped: set[str] = field(default_factory=set)

    @property
    def truncated(self) -> bool:
        return bool(self.stopped)


def has_value_edges(con: sqlite3.Connection) -> bool:
    return bool(
        con.execute(
            "select count(*) from sqlite_master where type=? and name=?",
            ("table", "data_flow"),
        ).fetchone()[0]
    )


# Roles that count as a contact, and roles that count as driving an output.
# `strict_bit` narrows the walk to those: a word device mentioned by an
# instruction is not a contact, and following it as one blurs a bit trace.
CONTACT_ROLES = ("a", "b")
DRIVER_ROLES = ("c", "SET", "RST", "PLS", "PLF", "OUT__16", "OUTH__16")


def successors(
    con: sqlite3.Connection, device: str, with_flow: bool, strict_bit: bool = False
) -> list[tuple[Any, str]]:
    """Everything one device leads to, and on what basis.

    Two bases, kept apart on purpose. A transfer says the value goes there; two
    devices on one rung say only that they appear together, which is true and
    much weaker. Collapsing them was the thing #36 existed to stop.
    """
    read_access = ("read",) if strict_bit else ("read", "ref", "both")
    roles = ""
    params: list[Any] = [device, *read_access, "write", "both"]
    if strict_bit:
        roles = (
            f" and r.role in ({','.join('?' * len(CONTACT_ROLES))})"
            f" and w.role in ({','.join('?' * len(DRIVER_ROLES))})"
        )
        params.extend([*CONTACT_ROLES, *DRIVER_ROLES])
    rows = con.execute(
        f"""
        select distinct w.device as device, w.comment as comment, w.pou as pou,
               w.step as step, w.role as role
        from xref r join xref w on r.lddb = w.lddb and r.pos = w.pos
        where r.device = ? and r.access in ({','.join('?' * len(read_access))})
          and w.access in (?, ?) and w.device <> r.device
          {roles}
        """,
        params,
    ).fetchall()
    flow: dict[str, Any] = {}
    if with_flow:
        for row in con.execute(
            """
            select f.destination_device as device, f.destination_comment as comment,
                   f.pou as pou, f.step as step, f.opcode as role
            from data_flow f where f.source_device = ?
            """,
            (device,),
        ):
            flow.setdefault(str(row["device"]), row)

    # A device reachable both ways is reported as the transfer. "They share a
    # rung" is true of a transfer too and says much less, so letting whichever
    # row came back first decide would hide the stronger fact -- which it did:
    # every entry read "same-rung" once the two lists were merged by order.
    out: list[tuple[Any, str]] = []
    for row in rows:
        name = str(row["device"])
        if name in flow:
            edge = flow.pop(name)
            out.append((edge, f"via {edge['role']}"))
        else:
            out.append((row, SAME_RUNG))
    for name, row in flow.items():
        out.append((row, f"via {row['role']}"))
    return out


def reach(
    con: sqlite3.Connection,
    devices: list[str] | str,
    max_depth: int,
    max_nodes: int,
    strict_bit: bool = False,
) -> Reach:
    """Walk outward from one device, or from a run of them.

    A block instruction writes a run: `BMOV D300 D400 K4` reaches D400 through
    D403, and starting only from the first of them loses whatever reads the
    rest. Callers pass the whole run.
    """
    starts = [devices] if isinstance(devices, str) else list(devices)
    seen = set(starts)
    result = Reach()
    frontier = [(name, 0) for name in starts]
    with_flow = has_value_edges(con)

    while frontier:
        current, depth = frontier.pop(0)
        candidates = successors(con, current, with_flow, strict_bit)
        unseen = [(row, basis) for row, basis in candidates if str(row["device"]) not in seen]

        if depth >= max_depth:
            # The walk ends here. Whether that lost anything is a question
            # about this node, not about the depth number.
            if unseen:
                result.stopped.add("max-depth")
            continue

        for row, basis in unseen:
            name = str(row["device"])
            if name in seen:
                continue  # an earlier sibling in this same batch reached it
            if len(result.steps) >= max_nodes:
                result.stopped.add("max-nodes")
                break
            seen.add(name)
            result.steps.append(
                Step(
                    device=name,
                    comment=str(row["comment"] or ""),
                    source=current,
                    basis=basis,
                    pou=str(row["pou"] or ""),
                    step=row["step"],
                    depth=depth + 1,
                )
            )
            frontier.append((name, depth + 1))
        if "max-nodes" in result.stopped:
            break
    return result
