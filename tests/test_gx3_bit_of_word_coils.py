"""Distinct static word bits stay distinct through real LDDB and CLI paths."""
import contextlib
import csv
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gx3cli.gx3_rung_text import collect
from gx3cli.review_gx3_project import coil_device, load_rows, review_duplicate_coils
from test_gx3_digit_block_runs import const, dev, instruction, modified
from test_gx3_shared_reach import write_program


def bit_coil(bit, contact=1, opcode='c', number=0x120):
    return instruction(opcode, ['W', 'Dots'], [modified(number, bit)], contact=contact, coil=True)


def paired_coils(*, bits=(10, 10), contacts=(1, 2), indexed=False):
    """Two independent horizontal branches in ONE serialized LadderBlock."""
    header, elements = [], []
    for y, (bit, contact) in enumerate(zip(bits, contacts)):
        tokens = ['a', 'M', 'c', 'W', 'Dots'] + (['Zs'] if indexed else [])
        header.extend(tokens)
        target = modified(0x120, bit)
        if indexed:
            target = f'M{{b={target}:m={dev(1)}}}'
        elements.extend([
            f'e{{s=ce{{op=ct{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[{dev(contact)}]}}:pos=0,{y}}}',
            f'e{{s=wire:pos=1,{y}}}',
            f'e{{s=ce{{op=cl{{op=#:ct=a:as=[as{{vt=A16}}]}}:args=[{target}]}}:pos=2,{y}}}',
        ])
    lengths = ':'.join(str(len(token)) for token in header)
    return f"V1:{len(header)}:{lengths}:{':'.join(header)}:cb{{fg=fg{{dim=3x2:es=[{':'.join(elements)}]}}}}"


def qualified_bit_coil(kind, bit, *, contact=1, unit=0, modifier=''):
    if kind == 'UG':
        base = f'B{{b={dev(unit)}:e={dev(100)}:vt=i}}'
        tokens = ['U', 'G', 'Dots']
    else:
        base = dev(0x120 if kind == 'W' else 120)
        tokens = [kind, 'Dots']
    target = f'M{{b={base}:m={const(bit)}}}'
    if modifier:
        tokens.append('Zs' if modifier == 'indexed' else 'Ats')
        target = f"M{{b={target}:m={dev(1) if modifier == 'indexed' else const(0)}}}"
    return instruction('c', tokens, [target], contact=contact, coil=True)


class BitCoilTests(unittest.TestCase):
    def test_buffer_and_word_bit_identity_with_different_conditions(self):
        from gx3cli.gx3_lint import LintContext, check_duplicate_coil
        for kind in ('UG', 'W', 'D'):
            for bit in (0, 10, 15):
                with self.subTest(kind=kind, bit=bit), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)/'source'
                    write_program(root, [(f'_guid/coil-{i}', qualified_bit_coil(kind, bit, contact=i)) for i in (1, 2)])
                    rows = load_rows(root, {})
                    base = {'UG': 'U0\\G100', 'W': 'W120', 'D': 'D120'}[kind]
                    expected = f'{base}.{bit:X}'
                    targets = [occ.device for row in rows for occ in row.occurrences if occ.access == 'write']
                    self.assertEqual(targets, [expected if kind == 'UG' else base] * 2)
                    found = review_duplicate_coils(rows, {})
                    self.assertEqual([(r['device'], r['driver_count'], r['severity']) for r in found],
                                     [(expected, 2, 'high')])
                    self.assertEqual([item.device for item in collect(root)], [expected] * 2)
                    self.assertEqual([r['device'] for r in check_duplicate_coil(LintContext(root, rows, {}))], [expected])

    def test_equal_buffer_bit_conditions_keep_the_normal_downgrade(self):
        for bit in (0, 10, 15):
            with self.subTest(bit=bit), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)/'source'
                data = qualified_bit_coil('UG', bit)
                write_program(root, [('_guid/first', data), ('_guid/second', data)])
                found = review_duplicate_coils(load_rows(root, {}), {})
                self.assertEqual([(r['device'], r['driver_count'], r['severity']) for r in found],
                                 [(f'U0\\G100.{bit:X}', 2, 'medium')])

    def test_distinct_buffer_bits_and_units_do_not_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'source'
            rungs = [(f'_guid/u{unit}-b{bit}', qualified_bit_coil('UG', bit, unit=unit, contact=unit+bit+1))
                     for unit in (0, 1) for bit in (0, 10, 15)]
            write_program(root, rungs)
            self.assertEqual(review_duplicate_coils(load_rows(root, {}), {}), [])
            with contextlib.closing(sqlite3.connect(root/'001_LDDB.db')) as con:
                con.execute('insert into LadderBlocks values (?, ?, 0, ?, 1, 0, 0)',
                            ('_guid/duplicate', 1000, qualified_bit_coil('UG', 10, contact=30)))
                con.commit()
            found = review_duplicate_coils(load_rows(root, {}), {})
            self.assertEqual([(r['device'], r['driver_count'], r['severity']) for r in found], [('U0\\G100.A', 2, 'high')])

    def test_indexed_and_indirect_buffer_bits_keep_the_decoder_identity(self):
        for kind in ('UG', 'W', 'D'):
            for modifier in ('indexed', 'indirect'):
                with self.subTest(kind=kind, modifier=modifier), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)/'source'
                    write_program(root, [(f'_guid/coil-{i}', qualified_bit_coil(kind, 10, contact=i, modifier=modifier)) for i in (1, 2)])
                    rows = load_rows(root, {})
                    targets = [occ for row in rows for occ in row.occurrences if occ.access == 'write']
                    self.assertEqual([coil_device(occ) for occ in targets], [occ.device for occ in targets])
                    self.assertEqual(len(targets), 2)
                    self.assertEqual({r['severity'] for r in review_duplicate_coils(rows, {})}, {'medium'})
                    self.assertNotIn('Z1', {r['device'] for r in review_duplicate_coils(rows, {})})

    def test_label_or_unknown_device_spaces_do_not_restore_a_bit_suffix(self):
        # Adapter boundary only. The physical-format integration paths above
        # are decoded from real serialized LDDB rows, without occurrence mocks.
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'source'
            write_program(root, [('_guid/buffer', qualified_bit_coil('UG', 10))])
            occ = next(o for o in load_rows(root, {})[0].occurrences if o.access == 'write')
            for kind in ('LABEL', '?', '', 'UNKNOWN'):
                adapted = replace(occ, device_type=kind, device='Area.Channel', detail='bit=10')
                self.assertEqual(coil_device(adapted), 'Area.Channel')

    def test_lint_and_rung_text_cli_preserve_a_qualified_buffer_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'source'
            write_program(root, [(f'_guid/coil-{i}', qualified_bit_coil('UG', 10, contact=i)) for i in (1, 2)])
            done = subprocess.run([sys.executable, '-m', 'gx3cli.gx3_cli', 'lint', str(root),
                                   '--checks', 'duplicate-coil', '--format', 'json', '--out-prefix', str(Path(tmp)/'lint')],
                                  capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            summary = json.loads(done.stdout)
            self.assertEqual(summary['checks']['duplicate-coil']['count'], 1)
            self.assertEqual(summary['checks']['duplicate-coil']['by_severity']['high'], 1)
            with (Path(tmp)/'lint_duplicate-coil.csv').open(encoding='utf-8-sig', newline='') as stream:
                finding, = csv.DictReader(stream)
            self.assertEqual(finding['device'], 'U0\\G100.A')
            done = subprocess.run([sys.executable, '-m', 'gx3cli.gx3_cli', 'rung-text', '--root', str(root),
                                   '--device', 'U0\\G100.A', '--format', 'json'],
                                  capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual([r['device'] for r in json.loads(done.stdout)], ['U0\\G100.A'] * 2)

    def test_read_only_index_registers_are_not_coil_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            write_program(root, [('_guid/indexed-pair', paired_coils(bits=(10, 11), indexed=True))])
            rows = load_rows(root, {})
            index_refs = [occ for occ in rows[0].occurrences if occ.device == 'Z1']
            self.assertEqual([occ.access for occ in index_refs], ['read', 'read'])
            found = review_duplicate_coils(rows, {})
            self.assertNotIn('Z1', {item['device'] for item in found})
            self.assertTrue(all(item['device'] == 'W120' for item in found), found)

    def test_independent_output_conditions_keep_high_severity_in_one_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            write_program(root, [('_guid/different-conditions', paired_coils())])
            self.assertEqual([(item.device, item.condition) for item in collect(root)],
                             [('W120.A', 'M1'), ('W120.A', 'M2')])
            found = review_duplicate_coils(load_rows(root, {}), {})
            self.assertEqual(len(found), 1)
            self.assertEqual((found[0]['device'], found[0]['driver_count'], found[0]['severity']),
                             ('W120.A', 2, 'high'))
            self.assertNotIn('not competing', found[0]['review_note'])
            done = subprocess.run([sys.executable, '-m', 'gx3cli.gx3_cli', 'lint', str(root),
                                   '--checks', 'duplicate-coil', '--format', 'json',
                                   '--out-prefix', str(Path(tmp)/'lint')],
                                  capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            summary = json.loads(done.stdout)
            self.assertEqual(summary['checks']['duplicate-coil']['by_severity']['high'], 1)

    def test_proven_equal_output_conditions_can_still_be_downgraded(self):
        for vertical in ('', ':vs=[v{pos=0,1}]'):
            with self.subTest(vertical=vertical), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'source'
                write_program(root, [('_guid/same-conditions', paired_coils(contacts=(1, 1)) + vertical)])
                self.assertEqual([item.condition for item in collect(root)], ['M1', 'M1'])
                found = review_duplicate_coils(load_rows(root, {}), {})
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]['severity'], 'medium')

    def test_missing_or_invalid_wire_positions_do_not_prove_equal_outputs(self):
        for first, second, contacts in [('unavailable', 'unavailable', (1, 2)), ('-1,0', '-1,1', (1, 2)),
                                        ('3,0', '3,1', (1, 2)), ('1,99', '1,99', (1, 2)),
                                        ('1,bad', '1,bad', (1, 2)), ('1,0junk', '1,1junk', (1, 1))]:
            with self.subTest(first=first, second=second), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'source'
                data = paired_coils(contacts=contacts).replace('pos=1,0', 'pos=' + first).replace('pos=1,1', 'pos=' + second)
                write_program(root, [('_guid/unknown-wires', data)])
                rows = load_rows(root, {})
                self.assertEqual(rows[0].parse_status, 'exact')
                found = review_duplicate_coils(rows, {})
                self.assertEqual(len(found), 1)
                self.assertEqual((found[0]['device'], found[0]['driver_count'], found[0]['severity']),
                                 ('W120.A', 2, 'high'))

    def test_unknown_vertical_geometry_does_not_prove_equal_outputs(self):
        for vertical in ('v{pos=unavailable}', 'v{pos=1,99}'):
            with self.subTest(vertical=vertical), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'source'
                data = paired_coils(contacts=(1, 1)) + ':vs=[' + vertical + ']'
                write_program(root, [('_guid/unknown-vertical', data)])
                found = review_duplicate_coils(load_rows(root, {}), {})
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]['severity'], 'high')

    def test_missing_condition_geometry_does_not_prove_equal_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            data = paired_coils(contacts=(1, 1)).replace('pos=0,1', 'pos=unavailable')
            write_program(root, [('_guid/unknown-condition', data)])
            found = review_duplicate_coils(load_rows(root, {}), {})
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]['severity'], 'high')

    def test_sixteen_bits_stay_distinct_and_word_filter_includes_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            write_program(root, [(f'_guid/bit-{bit}', bit_coil(bit)) for bit in range(16)])
            items = collect(root)
            self.assertEqual([item.device for item in items], [f'W120.{bit:X}' for bit in range(16)])
            self.assertTrue(all(item.to_line().endswith('-> ' + item.device) for item in items))
            self.assertEqual(len(collect(root, device='w120')), 16)
            self.assertEqual([item.device for item in collect(root, device='W120.A')], ['W120.A'])
            self.assertEqual(collect(root, device='W12'), [])
            self.assertEqual(review_duplicate_coils(load_rows(root, {}), {}), [])
            done = subprocess.run([sys.executable, '-m', 'gx3cli.gx3_cli', 'rung-text', '--root', str(root),
                                   '--device', 'W120.A', '--format', 'json'], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual([item['device'] for item in json.loads(done.stdout)], ['W120.A'])

    def test_same_bit_has_two_coils_and_retains_high_severity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            write_program(root, [('_guid/one', bit_coil(10, contact=1)), ('_guid/two', bit_coil(10, contact=2)),
                                 ('_guid/other-bit', bit_coil(11, contact=3))])
            found = review_duplicate_coils(load_rows(root, {}), {})
            self.assertEqual(len(found), 1)
            self.assertEqual((found[0]['device'], found[0]['driver_count'], found[0]['severity']), ('W120.A', 2, 'high'))

    def test_indexed_bits_are_not_relabelled_as_static_bits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            raw = f'M{{b={modified(0x120, 10)}:m={dev(1)}}}'
            write_program(root, [('_guid/indexed', instruction('c', ['W', 'Dots', 'Zs'], [raw], coil=True))])
            self.assertEqual(collect(root, device='W120.A'), [])

    def test_two_operations_in_one_row_are_two_coil_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            header = ['a', 'M', 'c', 'W', 'Dots', 'c', 'W', 'Dots']
            lengths = ':'.join(str(len(t)) for t in header)
            body = bit_coil(10).split(':cb{', 1)[1].replace('dim=3x1', 'dim=3x2')
            extra = f'e{{s=ce{{op=cl{{op=#:ct=a:as=[as{{vt=A16}}]}}:args=[{modified(0x120, 10)}]}}:pos=2,1}}'
            data = f"V1:8:{lengths}:{':'.join(header)}:cb{{" + body.replace(']}}', ':' + extra + ']}}')
            write_program(root, [('_guid/two-coils', data)])
            found = review_duplicate_coils(load_rows(root, {}), {})
            self.assertEqual(len(found), 1)
            self.assertEqual((found[0]['device'], found[0]['driver_count']), ('W120.A', 2))


if __name__ == '__main__':
    unittest.main()
