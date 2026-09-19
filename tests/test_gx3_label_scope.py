"""Distinct label tables must not merge into one checked device trace."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_label_resolve import LabelRef, LabelResolver
from test_gx3_label_resolve import _rung, LABEL_ID
from test_gx3_shared_reach import write_program


def fixture(root):
    for index, label_id in enumerate(('101', '202')):
        part = root/str(index)
        write_program(part, [('label-row', _rung(('a', 2), ('c', 7)).replace(LABEL_ID, label_id))])
        (part/'001_LDDB.db').rename(root/f'{index:03}_LDDB.db')
        part.rmdir()
    con = sqlite3.connect(root/'LabelData.db')
    con.execute('create table ColumnDataTbl(LabelID text,RowID integer,ColumnID integer,ColumnStrValue text)')
    con.execute('create table RowTbl(LabelID text,RowID integer,RowNo integer)')
    con.execute('create table DeviceAssignTbl(LabelID text,RowID integer,MELSECDevice text)')
    for scope in ('101', '202'):
        for row, name in ((2, 'Permit'), (7, 'Local')):
            con.execute('insert into ColumnDataTbl values(?,?,2,?)', (scope,row,name))
            con.execute('insert into RowTbl values(?,?,?)', (scope,row,row))
    con.commit()
    con.close()


def check_cli():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work/'project'
        fixture(root)
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]), 'PYTHONIOENCODING': 'utf-8'}
        def cli(*args):
            done = subprocess.run([sys.executable, '-m', 'gx3cli.gx3_cli', *args], cwd=work, env=env, capture_output=True, text=True, encoding='utf-8')
            assert done.returncode == 0, done.stdout+done.stderr
        for target in ('Local', 'Local@101', 'Local@202'):
            report = work/(target+'.json')
            cli('trace-device', target, '--root', str(root), '--strict-logic', '--format', 'json', '-o', str(report))
            result = json.loads(report.read_text(encoding='utf-8'))
            if target == 'Local':
                assert result['analysis']['state'] == 'not_evaluated', result
                assert 'multiple scopes' in result['analysis']['reason']
                assert 'Local@101' in result['analysis']['next_step']
                assert result['driver_rows'] == []
            else:
                assert result['analysis']['state'] == 'checked', result
                assert len(result['driver_rows']) == 1, result
                row = result['driver_rows'][0]
                assert row['device'] == target
                assert {c['device'] for c in row['conditions']} == {'Permit@'+target.split('@')[1]}
        db = work/'xref.sqlite'
        cli('xref', 'build', '--root', str(root), '--db', str(db))
        con = sqlite3.connect(db)
        try:
            assert dict(con.execute('select device,count(*) from xref group by device')) == {
                'Local@101':1, 'Local@202':1, 'Permit@101':1, 'Permit@202':1}
        finally:
            con.close()


def check_aliases():
    ref = LabelRef('Unique')
    labels = LabelResolver({('101', 2):ref, ('101', 20):ref})
    assert labels.get('101', 2).name == 'Unique'
    assert labels.get('101', 20).name == 'Unique'
    assert not labels.ambiguous_names
    # Independent resolvers must not change one another or caller-owned refs.
    collision = LabelResolver({('101', 2):ref, ('202', 2):ref})
    assert collision.get('101', 2).name == 'Unique@101'
    assert labels.get('101', 2).name == ref.name == 'Unique'


if __name__ == '__main__':
    check_cli()
    check_aliases()
    print('label scope checks passed')
