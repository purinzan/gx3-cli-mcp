"""Synthetic listed-instruction fixtures; no project data."""
import csv
import tempfile
from pathlib import Path

from gx3cli.gx3_ladder_csv import Instruction, parse_instructions
from gx3cli.gx3_rung_text import collect_csv, to_json


def parse(lines):
    return parse_instructions([Instruction('main', n, '', line.split()[0], tuple(line.split()[1:]))
                               for n, line in enumerate(lines.splitlines()) if line.strip()])


def test_blocks_and_saved_results():
    items = parse('LD X0\nOR X1\nLD X2\nANI X3\nORB\nMPS\nAND X4\nOUT Y0\nMRD\nAND X5\nOUT Y1\nMPP\nANI X6\nOUT Y2\nLD X7\nOUT Y3\nEND')
    assert [i.condition for i in items] == [
        '(X0 OR X1 OR X2 AND /X3) AND X4',
        '(X0 OR X1 OR X2 AND /X3) AND X5',
        '(X0 OR X1 OR X2 AND /X3) AND /X6', 'X7']
    items = parse('LD X0\nOR X1\nLD X2\nOR X3\nLD X4\nOR X5\nANB\nANB\nOUT Y0')
    assert items[0].condition == '(X0 OR X1) AND ((X2 OR X3) AND (X4 OR X5))'


def test_nested_saved_results():
    items = parse('LD X0\nMPS\nAND X1\nMPS\nAND X2\nOUT Y0\nMPP\nAND X3\nOUT Y1\nMPP\nAND X4\nOUT Y2')
    assert [i.condition for i in items] == ['X0 AND X1 AND X2', 'X0 AND X1 AND X3', 'X0 AND X4']


def test_edges_comparison_and_destinations():
    items = parse('LDP X0\nAND>= D0 K10\nBMOV D0 D20 K4\nFROM K0 K1 D100 K2\nTO K0 K1 D100 K2\nLDI X1\nINV\nOUT Y1')
    assert items[0].condition == 'RISE(X0) AND >=(D0, K10)'
    assert [i.device for i in items] == ['D20', 'D100', '', 'Y1']
    assert items[-1].condition == 'NOT(/X1)'


def test_unsupported_is_visible_and_not_treated_as_exact():
    items = parse('LD X0\nUNSUPPORTED D0\nOUT Y0\nLD X1\nOUT Y1')
    assert len(items) == 3
    assert all(i.diagnostic and i.condition.startswith('?') for i in items)
    for op in ('MC N0 M0', 'CALL P0', 'CJ P0'):
        controlled = parse('LD X0\n' + op + '\nLD X1\nOUT Y1')
        assert controlled[-1].diagnostic.startswith('unsupported control flow')


def test_bad_stack_fails_instead_of_guessing():
    for source in ['LD X0\nMPP', 'LD X0\nANB', 'LD X0\nMPS\nOUT Y0', 'LD X0\nLD X1\nOUT Y0\nEND', 'AND X0\nOUT Y0', 'LD X0\nMOV D0']:
        try:
            parse(source)
        except ValueError:
            pass
        else:
            raise AssertionError(source)


def test_mitsubishi_en_ja_encoding_and_continuation():
    for encoding in ('utf-16', 'utf-8-sig', 'cp932'):
        for header in [('Step No.', 'Line Statement', 'Instruction', 'I/O (Device)'),
                       ('ステップ番号', '行間ステートメント', '命令', 'I/O(デバイス)')]:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'synthetic.csv'
                with path.open('w', encoding=encoding, newline='') as stream:
                    writer = csv.writer(stream, delimiter='\t')
                    writer.writerows([['Synthetic'], ['Module Type Information: RCPU'], header,
                                      ['0 [Title] Demo', '', '', ''], ['0', '', 'LD', 'X0'],
                                      ['1', '', 'MOV', 'D0'], ['', '', '', 'D10'],
                                      ['4', '', 'BMOV', 'D1'], ['', '', '', 'D20'], ['', '', '', 'K4'],
                                      ['9', '', 'END', '']])
                items = collect_csv(path)
                assert [(i.opcode, i.device) for i in items] == [('MOV', 'D10'), ('BMOV', 'D20')]
                assert all(i.title == 'Demo' and i.condition == 'X0' for i in items)
                assert items[0].to_line().endswith('MOV D0 D10')
                assert to_json(items)[1]['operands'] == ['D1', 'D20', 'K4']
                assert len(collect_csv(path, 'd20')) == 1


def test_string_literal_operand():
    from gx3cli.gx3_ladder_csv import decode_instructions
    items = decode_instructions([{'Instruction': 'MOV', 'Operand': '"Hello world" D0'}], 'main')
    assert items[0].operands == ('"Hello world"', 'D0')


def test_binary_reader_matches_csv_for_synthetic_logic():
    from gx3cli.gx3_intermediate_tool import generate_rung
    from gx3cli.gx3_rung_text import rung_texts
    from gx3cli.review_gx3_project import LadderRow
    cases = [
        ({"device": "X0"}, 'LD X0'),
        ({"and": [{"device": "X0"}, {"not": {"device": "X1"}}]}, 'LD X0\nANI X1'),
        ({"or": [{"device": "X0"}, {"device": "X1"}]}, 'LD X0\nOR X1'),
    ]
    for logic, source in cases:
        data, rowsize, _ = generate_rung(logic, {"type": "coil", "device": "Y0"})
        row = LadderRow(block_id='synthetic', lddb='main', pos=0, title='',
                        blocktype=0, rowsize=rowsize, data=data, dim='',
                        operations=[], parse_status='')
        binary = rung_texts(row)
        listed = parse(source + '\nOUT Y0')
        assert [(i.condition, i.device) for i in binary] == [(i.condition, i.device) for i in listed]


def test_rejects_corrupt_csv_without_losing_values():
    from gx3cli.gx3_ladder_csv import read_csv_records, operands
    for source in ('LD X0\nMOV D0 D10 K4', 'LD X0\nMPS D0\nMPP', 'LD X0\nNOP D0'):
        try:
            parse(source)
        except ValueError:
            pass
        else:
            raise AssertionError(f'malformed instruction accepted: {source}')
    for text in ('Instruction,Operand\nMOV,D0,D10\n',
                 'Instruction,Operand,Operand\nMOV,D0,D10\n'):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'malformed.csv'
            path.write_text(text, encoding='utf-8')
            try:
                read_csv_records(path)
            except ValueError:
                pass
            else:
                raise AssertionError('CSV fields were silently discarded')
    try:
        operands('"unterminated D0')
    except ValueError:
        pass
    else:
        raise AssertionError('unterminated string accepted')


def test_full_operand_inventory_survives_csv_decode():
    from gx3cli.gx3_ladder_csv import read_csv_records, decode_instructions
    expected = [
        ('LD', ('X0',)), ('MOV', ('D0', 'D10')),
        ('BMOV', ('D1', 'D20', 'K4')), ('FMOV', ('K0', 'D30', 'K8')),
        ('FROM', ('K0', 'K1', 'D100', 'K2')), ('TO', ('K0', 'K1', 'D100', 'K2')),
        ('XCH', ('D40', 'D50')), ('$MOV', ('"Text, with spaces"', 'D60')),
        ('MOV', ('U0\\G100Z2', 'D70Z1')), ('MOV', ('K4M0', 'D80')),
        ('OUT', ('T0', 'K10')), ('END', ()),
    ]
    for encoding, delimiter in [('utf-16', '\t'), ('cp932', '\t'), ('utf-8-sig', ',')]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inventory.csv'
            with path.open('w', newline='', encoding=encoding) as stream:
                writer = csv.writer(stream, delimiter=delimiter)
                writer.writerow(['Synthetic'])
                writer.writerow(['Module Type Information: RCPU'])
                writer.writerow(['Step No.', 'Instruction', 'I/O (Device)'])
                for step, (op, args) in enumerate(expected):
                    writer.writerow([step, op, args[0] if args else ''])
                    for arg in args[1:]:
                        writer.writerow(['', '', arg])
            decoded = decode_instructions(read_csv_records(path), path.name)
            assert [(i.opcode, i.operands) for i in decoded] == expected
            outputs = collect_csv(path)
            # XCH has two write targets; TO remains visible without a local target.
            assert [i.device for i in outputs] == [
                'D10', 'D20', 'D30', 'D100', '', 'D40', 'D50', 'D60', 'D70Z1', 'D80', 'T0']
            assert all(i.condition == 'X0' and not i.diagnostic for i in outputs)
            assert outputs[-1].to_line().endswith('OUT T0 K10')


def test_truth_tables_csv_binary_and_independent_spec_agree():
    """101 synthetic outputs, 1184 exhaustive input assignments.

    The fixture spec, CSV instruction folding, and GX3 geometric parser are
    independent paths. Comparison is semantic: DNF rewrites may change text.
    This does NOT claim GX Works3 export/import or temporal equivalence.
    """
    import ast
    import itertools
    import re
    from gx3cli.gx3_intermediate_tool import generate_rung
    from gx3cli.gx3_rung_text import rung_texts
    from gx3cli.review_gx3_project import LadderRow

    def leaf(name, inverted=False):
        result = {'device': name}
        return {'not': result} if inverted else result

    def join(op, *terms):
        return {op: list(terms)}

    def specification(node, values):
        if 'device' in node:
            return values[node['device']]
        if 'not' in node:
            return not specification(node['not'], values)
        op = 'and' if 'and' in node else 'or'
        parts = [specification(child, values) for child in node[op]]
        return all(parts) if op == 'and' else any(parts)

    def rendered_tree(text):
        text = text.replace('AND', 'and').replace('OR', 'or').replace('NOT', 'not')
        text = re.sub(r'/([A-Z][A-Z0-9]*)', r'(not \1)', text)
        return ast.parse(text, mode='eval').body

    def evaluate(node, values):
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not evaluate(node.operand, values)
        if isinstance(node, ast.BoolOp):
            parts = [evaluate(child, values) for child in node.values]
            return all(parts) if isinstance(node.op, ast.And) else any(parts)
        raise AssertionError(ast.dump(node))

    cases = []
    for inverted in itertools.product((False, True), repeat=3):
        nodes = [leaf(f'X{i}', invert) for i, invert in enumerate(inverted)]
        ld = [f'LD{"I" if invert else ""} X{i}' for i, invert in enumerate(inverted)]
        for a, b in itertools.product(('and', 'or'), repeat=2):
            for right_nested in (False, True):
                if right_nested:
                    tree = join(a, nodes[0], join(b, nodes[1], nodes[2]))
                    source = '\n'.join(ld + ['ANB' if b == 'and' else 'ORB', 'ANB' if a == 'and' else 'ORB'])
                else:
                    tree = join(b, join(a, nodes[0], nodes[1]), nodes[2])
                    source = '\n'.join(ld[:2] + ['ANB' if a == 'and' else 'ORB', ld[2], 'ANB' if b == 'and' else 'ORB'])
                cases.append((source + '\nOUT Y0', [tree], 3))
    for inverted in itertools.product((False, True), repeat=4):
        nodes = [leaf(f'X{i}', invert) for i, invert in enumerate(inverted)]
        for op in ('and', 'or'):
            other = 'or' if op == 'and' else 'and'
            lines = []
            for i, invert in enumerate(inverted):
                contact = 'LD' if i % 2 == 0 else ('OR' if other == 'or' else 'AND')
                if invert:
                    contact = 'ANI' if contact == 'AND' else contact + 'I'
                lines.append(f'{contact} X{i}')
            lines += ['ANB' if op == 'and' else 'ORB', 'OUT Y0']
            cases.append(('\n'.join(lines), [join(op, join(other, *nodes[:2]), join(other, *nodes[2:]))], 4))
    cases.append((
        'LD X0\nMPS\nAND X1\nMPS\nAND X2\nOUT Y0\nMRD\nANI X3\nOUT Y1\nMPP\nOR X4\nOUT Y2\nMRD\nAND X3\nOUT Y3\nMPP\nANI X4\nOUT Y4',
        [join('and', leaf('X0'), leaf('X1'), leaf('X2')),
         join('and', leaf('X0'), leaf('X1'), leaf('X3', True)),
         join('or', join('and', leaf('X0'), leaf('X1')), leaf('X4')),
         join('and', leaf('X0'), leaf('X3')),
         join('and', leaf('X0'), leaf('X4', True))], 5))
    outputs_checked = assignments_checked = 0
    for source, specs, inputs in cases:
        listed = parse(source)
        assert len(listed) == len(specs)
        for index, (item, spec) in enumerate(zip(listed, specs)):
            target = f'Y{index}'
            assert item.device == target and item.instruction.opcode == 'OUT'
            assert item.instruction.operands == (target,) and not item.diagnostic
            data, rowsize, _ = generate_rung(spec, {'type': 'coil', 'device': target})
            row = LadderRow(block_id='synthetic', lddb='main', pos=0, title='',
                            blocktype=0, rowsize=rowsize, data=data, dim='', operations=[], parse_status='')
            binary = rung_texts(row)
            assert len(binary) == 1 and binary[0].device == target
            csv_tree, binary_tree = rendered_tree(item.condition), rendered_tree(binary[0].condition)
            for bits in itertools.product((False, True), repeat=inputs):
                values = {f'X{i}': value for i, value in enumerate(bits)}
                expected = specification(spec, values)
                assert evaluate(csv_tree, values) == expected, (source, values, item.condition)
                assert evaluate(binary_tree, values) == expected, (source, values, binary[0].condition)
                assignments_checked += 1
            outputs_checked += 1
    assert (outputs_checked, assignments_checked) == (101, 1184)
    print('evidence: 101 synthetic outputs / 1184 input assignments matched CSV, GX3 and independent specification')


def test_data_instructions_match_binary_all_operands_and_targets():
    from gx3cli.gx3_ladder_print import parse_rung
    from gx3cli.gx3_rung_text import rung_texts
    from gx3cli.review_gx3_project import LadderRow
    cases = [
        ('MOV', ('D0', 'D10'), ('D10',)),
        ('BMOV', ('D0', 'D10', 'K4'), ('D10',)),
        ('FMOV', ('K0', 'D20', 'K8'), ('D20',)),
        ('FROM', ('K0', 'K1', 'D100', 'K2'), ('D100',)),
        ('XCH', ('D0', 'D10'), ('D0', 'D10')),
    ]
    for op, args, targets in cases:
        # Synthetic LDDB elements, independent of CSV parsing or encoding.
        types = ['K_1' if a.startswith('K') else 'D' for a in args]
        serialized = ['c{s=#:v=' + a[1:] + '}' if a.startswith('K') else
                      'd{s=#:a=' + a[1:] + ':vt=nn}' for a in args]
        data = (
            'V1:6:1:1:1:1:a:X:' + op + ':' + ':'.join(types) + ':cb{fg=fg{dim=2x1:es=['
            'e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=0:vt=nn}]}:pos=0,0}:'
            'e{s=ce{op=in{op=#:ct=a:as=[as{vt=A16}]}:args=[' + ':'.join(serialized) + ']}:pos=1,0}]}}'
        )
        row = LadderRow(block_id='synthetic', lddb='main', pos=0, title='',
                        blocktype=0, rowsize=1, data=data, dim='2x1', operations=[], parse_status='')
        raw = parse_rung(row)[0][-1]
        csv_items = parse('LD X0\n' + op + ' ' + ' '.join(args))
        assert raw.role == op and tuple(raw.operands) == args
        assert tuple(i.device for i in csv_items) == targets
        assert all(i.instruction.operands == args and i.instruction.opcode == op for i in csv_items)
        assert [(i.opcode, i.device, i.condition) for i in rung_texts(row)] == [
            (i.instruction.opcode, i.device, i.condition) for i in csv_items]
    print('evidence: 5 data-instruction fixtures matched GX3/CSV opcode, all operands, targets and conditions')


def main():
    tests = [v for k, v in globals().items() if k.startswith('test_') and callable(v)]
    for test in tests:
        test()
    print(f'{len(tests)} ladder CSV checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
