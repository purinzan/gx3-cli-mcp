"""Synthetic serialized LD -> decoder -> xref -> lint, including run ends."""
import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from gx3cli.gx3_arg_decode import parse_row_operations
from gx3cli.gx3_lint import LintContext, check_external_value_source
from gx3cli.gx3_xref_read import occurrences_of
from test_gx3_shared_reach import build_xref, write_program


def dev(number):
    return f"d{{s=#:a={number}:vt=nn}}"


def const(number):
    return f"c{{s=#:v={number}}}"


def modified(number, value):
    return f"M{{b={dev(number)}:m={const(value)}}}"


def instruction(opcode, tokens, args, *, contact=1, coil=False):
    header = ["a", "M", opcode, *tokens]
    lengths = ":".join(str(len(t.encode('utf-16-le')) // 2) for t in header)
    kind = "cl" if coil else "in"
    return (f"V1:{len(header)}:{lengths}:{':'.join(header)}:cb{{fg=fg{{dim=3x1:es=["
            f"e{{s=ce{{op=ct{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[{dev(contact)}]}}:pos=0,0}}:"
            "e{s=wire:pos=1,0}:"
            f"e{{s=ce{{op={kind}{{op=#:ct=a:as=[as{{vt=A16}}]}}:args=[{':'.join(args)}]}}:pos=2,0}}]}}}}")


def spans(data):
    operations, status = parse_row_operations(data)
    assert status == "exact", status
    return {a.device: (a.access, a.range_len) for a in operations[-1].args}


class DigitBlockTests(unittest.TestCase):
    def test_bmov_uses_each_operands_digit_width(self):
        data = instruction("BMOV", ["M", "Ks", "B", "Ks", "K_1"],
                           [modified(1000, 2), modified(0x200, 4), const(3)])
        self.assertEqual(spans(data), {"M1000": ("read", 24), "B200": ("write", 48)})

    def test_word_source_and_digit_destination_have_different_units(self):
        data = instruction("BMOV", ["W", "B", "Ks", "K_1"],
                           [dev(0x100), modified(0x200, 4), const(4)])
        self.assertEqual(spans(data), {"W100": ("read", 4), "B200": ("write", 64)})

    def test_fill_reads_one_digit_group_and_writes_n_groups(self):
        data = instruction("FMOV", ["M", "Ks", "B", "Ks", "K_1"],
                           [modified(1000, 2), modified(0x200, 4), const(3)])
        self.assertEqual(spans(data), {"M1000": ("read", 8), "B200": ("write", 48)})

    def test_one_element_keeps_its_digit_width(self):
        data = instruction("BMOV", ["M", "Ks", "B", "Ks", "K_1"],
                           [modified(1000, 2), modified(0x200, 4), const(1)])
        self.assertEqual(spans(data), {"M1000": ("read", 8), "B200": ("write", 16)})

    def test_double_fill_and_shift_counts_do_not_multiply_digit_width(self):
        double = instruction("DFMOV", ["D", "D", "K_1"], [dev(100), dev(200), const(5)])
        self.assertEqual(spans(double), {"D100": ("read", 2), "D200": ("write", 10)})
        rotate = instruction("ROR", ["M", "Ks", "K_1"], [modified(1000, 4), const(3)])
        self.assertEqual(spans(rotate)['M1000'][1], 16)
        shift = instruction("SFTL", ["M", "Ks", "M", "Ks", "K_1", "K_1"],
                            [modified(100, 4), modified(200, 4), const(4), const(3)])
        self.assertEqual(spans(shift)["M200"][1], 16)  # no guessed digit*n1*n2 range

    def test_device_held_counts_and_indexed_bases_have_no_static_run(self):
        unknown = instruction('BMOV', ['M', 'Ks', 'B', 'Ks', 'D'],
                              [modified(1000, 2), modified(0x200, 4), dev(99)])
        self.assertEqual(spans(unknown)['B200'][1], 0)
        indexed = f'M{{b={modified(0x200, 4)}:m={dev(2)}}}'
        data = instruction('BMOV', ['D', 'B', 'Ks', 'Zs', 'K_1'], [dev(100), indexed, const(4)])
        self.assertEqual(spans(data)['B200'][1], 1)
        self.assertEqual(spans(data)['Z2'], ('read', 1))

    def test_last_bit_is_searchable_and_the_next_bit_is_not(self):
        for count in (1, 4, 100):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                data = instruction("BMOV", ["W", "B", "Ks", "K_1"],
                                   [dev(0x100), modified(0x200, 4), const(count)])
                root = work / "source"
                write_program(root, [("_guid/synthetic", data)])
                db = build_xref(root, work / "xref.sqlite")
                with contextlib.closing(sqlite3.connect(db)) as con:
                    con.row_factory = sqlite3.Row
                    last = f"B{0x200 + count * 16 - 1:X}"
                    after = f"B{0x200 + count * 16:X}"
                    self.assertEqual(len(occurrences_of(con, last, access=['write'])), 1)
                    self.assertEqual(occurrences_of(con, after, access=['write']), [])

    def test_written_members_reach_external_value_source_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            root = work / "source"
            write_program(root, [("_guid/block", instruction("BMOV", ['D', 'D', 'K_1'], [dev(100), dev(400), const(4)])),
                                 ("_guid/inside", instruction("MOV", ['D', 'D'], [dev(403), dev(900)])),
                                 ("_guid/outside", instruction("MOV", ['D', 'D'], [dev(404), dev(901)]))])
            db = build_xref(root, work / "xref.sqlite")
            refresh = work / "refresh.csv"
            refresh.write_text('device_start,device_end\n', encoding='utf-8')
            with contextlib.closing(sqlite3.connect(db)) as con:
                con.row_factory = sqlite3.Row
                ctx = LintContext(root, [], {}, xref=con, refresh_csv=str(refresh))
                self.assertEqual({r['device'] for r in check_external_value_source(ctx)}, {'D100', 'D404'})
                from gx3cli.gx3_xref_read import written_devices
                self.assertTrue({'D400', 'D401', 'D402', 'D403'} <= written_devices(con))
                con.execute('drop table xref_members')
                self.assertEqual(written_devices(con), {'D400', 'D900', 'D901'})

    def test_an_empty_member_index_does_not_fall_back_to_named_writers(self):
        # Reader contract only; the integration test above builds real members.
        from gx3cli.gx3_xref_read import written_devices
        with contextlib.closing(sqlite3.connect(':memory:')) as con:
            con.execute('create table xref(id integer, device text, access text)')
            con.execute("insert into xref values (1, 'D400', 'write')")
            con.execute('create table xref_members(src_id integer, member_device text)')
            self.assertEqual(written_devices(con), set())


if __name__ == '__main__':
    unittest.main()
