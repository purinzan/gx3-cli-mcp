"""The real xref build shares row decoding, including partial-result evidence."""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from gx3cli import gx3_data_flow as flow, gx3_xref as xref
from gx3cli.gx3_intermediate_tool import read_ladder_rows
from test_gx3_flow_in_xref import a_project


def check_build(partial):
    with tempfile.TemporaryDirectory() as tmp:
        root=a_project(Path(tmp))
        db=Path(tmp)/'xref.sqlite'
        raw=read_ladder_rows(root)
        expected_rows=sum(r['blocktype']==0 for rows in raw.values() for r in rows)
        real_decode=flow.parse_row_operations
        def decode(data,labels):
            ops,status=real_decode(data,labels)
            return ops,'partial' if partial else status
        # Standalone data-flow is an independent consumer of the shared row
        # iterator. Its complete persisted edge columns must agree with xref.
        with patch.object(flow,'parse_row_operations',side_effect=decode):
            expected_edges=xref.flow_edge_rows(root)
        assert bool(expected_edges) is not partial
        with patch.object(flow,'parse_row_operations',side_effect=decode) as decoder, \
             patch.object(xref,'read_ladder_rows',wraps=read_ladder_rows) as loader, \
             patch.object(flow,'read_ladder_rows',side_effect=AssertionError('second source read')), \
             patch.object(flow,'build_report',side_effect=AssertionError('second report/decode')):
            assert xref.main(['--root',str(root),'--db',str(db),'build'])==0
            assert decoder.call_count==expected_rows
            assert loader.call_count==1
        con=sqlite3.connect(db)
        try:
            actual=[tuple(r[1:]) for r in con.execute('select * from data_flow order by id')]
            assert actual==expected_edges
            assert con.execute('select count(*) from xref').fetchone()[0]>0
            statuses={r[0] for r in con.execute('select distinct parse_status from xref')}
            assert statuses==({'partial'} if partial else {'exact'})
        finally:con.close()


if __name__=='__main__':
    check_build(False)
    check_build(True)
    print('shared xref decoding checks passed')
