"""Exercise CSV export through the CLI from a synthetic GX3 archive."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from gx3cli.gx3_csv_export import export_csv
from gx3cli.gx3_synthetic_project import create_demo_line_project
from gx3cli.review_gx3_project import load_rows
from gx3cli.gx3_ladder_print import parse_rung


def read(path):
    with path.open(encoding='utf-16',newline='') as stream:
        return list(csv.reader(stream,delimiter='\t'))


def test_cli_archive_export_preserves_inventory_and_source():
    with tempfile.TemporaryDirectory() as tmp:
        work=Path(tmp);root=create_demo_line_project(work/'source',overwrite=True)
        archive=work/'demo.gx3'
        with zipfile.ZipFile(archive,'w') as z:
            for f in root.rglob('*'):
                if f.is_file():z.write(f,f.relative_to(root))
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        out=work/'csv'
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'PYTHONIOENCODING':'utf-8'}
        done=subprocess.run([sys.executable,'-m','gx3cli.gx3_cli','csv-export','--root',str(archive),'--output-dir',str(out)],cwd=work,env=env,capture_output=True,text=True,encoding='utf-8')
        assert done.returncode==0,done.stdout+done.stderr
        manifest=json.loads((out/'manifest.json').read_text())
        assert manifest['project']=='demo' and manifest['source_sha256']==digest
        assert manifest['ladder_importable'] is False
        ladder_files=[f for f in manifest['files'] if f['file'].startswith('ladder_')]
        expected=sum(len(parse_rung(r)[0]) for r in load_rows(root,{}))
        assert sum(f['rows'] for f in ladder_files)==expected and expected>0
        for f in ladder_files:
            rows=read(out/f['file']);header=rows[0]
            assert 'block_step' in header and 'instruction_step' not in header
            for row in rows[1:]:assert isinstance(json.loads(row[header.index('operands_json')]),list)
        comments=read(out/'COMMENT.csv')
        assert comments[0]==['demo'] and comments[1]==['デバイス名','コメント']
        assert len(comments)>2
        assert hashlib.sha256(archive.read_bytes()).hexdigest()==digest
        sentinel=(out/'COMMENT.csv').read_bytes()
        try:export_csv(root,out)
        except ValueError:pass
        else:raise AssertionError('overwrote existing destination')
        assert (out/'COMMENT.csv').read_bytes()==sentinel
        try:export_csv(root,work/'missing',program='not-a-program')
        except ValueError:pass
        else:raise AssertionError('unknown selector succeeded')
        assert not (work/'missing').exists()
        assert not list(work.glob('.gx3-csv-*'))


def test_non_ld_and_missing_comments_are_explicit():
    with tempfile.TemporaryDirectory() as tmp:
        root=create_demo_line_project(Path(tmp)/'source',overwrite=True)
        (root/'other_STDB.db').touch()
        for f in root.glob('*_DC.db'):f.unlink()
        manifest=export_csv(root,Path(tmp)/'out')
        assert manifest['status']=='partial'
        assert any('non-LD' in i['reason'] for i in manifest['issues'])
        assert any('comment database absent' in i['reason'] for i in manifest['issues'])
        try:export_csv(root,root/'out')
        except ValueError:pass
        else:raise AssertionError('wrote into project')




def test_text_quoting_and_failed_snapshot_publish():
    import sqlite3
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp:
        work=Path(tmp);root=create_demo_line_project(work/'source',overwrite=True)
        db=next(root.glob('*_DC.db'));con=sqlite3.connect(db)
        value='tab\tquote"\r\n日本語😀'
        con.execute('update COMMENT_DATA set CmtData=?',(value,));con.commit();con.close()
        result=export_csv(root,work/'text',kind='comments')
        rows=read(work/'text'/'COMMENT.csv')
        assert rows[2][1]==value
        assert result['ladder_importable'] is False
        with patch('gx3cli.gx3_csv_export.fingerprint',side_effect=['before','after']):
            try:export_csv(root,work/'changed',kind='comments')
            except ValueError as e:assert 'changed during export' in str(e)
            else:raise AssertionError('published changed snapshot')
        assert not (work/'changed').exists()
        assert not list(work.glob('.gx3-csv-*'))

if __name__=='__main__':
    test_cli_archive_export_preserves_inventory_and_source()
    test_non_ld_and_missing_comments_are_explicit()
    test_text_quoting_and_failed_snapshot_publish()
    print('CSV export checks passed')
