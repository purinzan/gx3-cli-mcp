"""Exercise shared decoding through CSV, lint, drawing, and logic consumers."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from gx3cli import gx3_arg_decode as decode, gx3_intermediate_tool as intermediate
from gx3cli import review_gx3_project as review, gx3_csv_export as export
from gx3cli.gx3_ladder_logic import positioned_elements
from gx3cli.gx3_ladder_print import parse_rung
from gx3cli.gx3_label_resolve import LabelResolver, LabelRef
from gx3cli.gx3_lint import LintContext, decode_row_ops
from test_gx3_flow_in_xref import a_project, MOV, BMOV
from test_gx3_native_csv import make_fixture
from test_gx3_label_resolve import _rung, LABEL_ID


def check_consumers():
    with tempfile.TemporaryDirectory() as tmp:
        root = a_project(Path(tmp))
        with patch.object(decode, 'parse_row_operations', wraps=decode.parse_row_operations) as parser, \
             patch.object(intermediate, 'parse_row_syntax', wraps=intermediate.parse_row_syntax) as syntax:
            # load_rows imports its syntax entry point directly.
            with patch.object(review, 'parse_row_syntax', syntax):
                rows = review.load_rows(root, {})
            ctx = LintContext(root, rows, {})
            for row in rows:
                assert ctx.ops_for(row) == decode_row_ops(row.data, decoded=decode.row_operations(row))
                parse_rung(row)
                positioned_elements(row)
            assert parser.call_count == len(rows)
            assert syntax.call_count == len(rows)
        for row in rows:
            assert row.operations == intermediate.operation_model(row.data)
            assert decode.row_operations(row) == decode.parse_row_operations(row.data)
            assert ctx.ops_for(row) == decode_row_ops(row.data)
        row = rows[0]
        original = decode.row_operations(row)
        original[0][0].args.clear()
        assert decode.row_operations(row) == decode.parse_row_operations(row.data)
        row.data = BMOV
        assert ctx.ops_for(row) == decode_row_ops(BMOV)
        assert decode.row_operations(row) == decode.parse_row_operations(BMOV)
        # Same location with a changed body must not reuse lint's old adapter.
        assert ctx.ops_for(row) != decode_row_ops(MOV)
        row.data = MOV.replace('s=ce{', 's=unknown{', 1)
        assert decode.row_operations(row)[1] == 'partial'


def check_resolvers():
    row = review.LadderRow('db', 0, 'id', '', 0, 1,
        _rung(('a', 1)), '', [], 'exact')
    first = LabelResolver({(LABEL_ID, 1): LabelRef('first')})
    second = LabelResolver({(LABEL_ID, 1): LabelRef('second')})
    with patch.object(decode, 'parse_row_operations', wraps=decode.parse_row_operations) as parser:
        a = decode.row_operations(row, first)
        b = decode.row_operations(row, second)
        assert a != b
        assert decode.row_operations(row, first) == a
        assert parser.call_count == 2


def check_csv_reads():
    with tempfile.TemporaryDirectory() as tmp:
        root, _, _ = make_fixture(Path(tmp))
        for fmt in ('gxworks3', 'analysis'):
            with patch.object(export, 'read_ladder_rows', wraps=intermediate.read_ladder_rows) as reader, \
                 patch.object(review, 'read_ladder_rows', side_effect=AssertionError('second DB read')), \
                 patch.object(decode, 'parse_row_operations', wraps=decode.parse_row_operations) as parser:
                export.export_csv(root, Path(tmp)/fmt, kind='ladder', format=fmt)
                assert reader.call_count == 1
                assert parser.call_count == 1
        with patch.object(review, 'read_ladder_rows', side_effect=AssertionError('empty input reread')):
            assert review.load_rows(root, {}, rows_by_db={}) == []


if __name__ == '__main__':
    check_consumers()
    check_resolvers()
    check_csv_reads()
    print('shared row analysis checks passed')
