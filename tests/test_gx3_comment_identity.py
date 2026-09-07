"""Synthetic SQLite records exercise comment readers without device aliasing."""
import sqlite3
import tempfile
from pathlib import Path
from gx3cli.gx3_comment_store import read_comment_records
from gx3cli.gx3_ladder_print import load_print_comments, comment_text_for
from gx3cli.review_gx3_project import load_comments_for_root


def test_word_bit_unit_and_deleted_comment_identity():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=root/'test_DC.db';con=sqlite3.connect(db)
        con.executescript('''CREATE TABLE DEVICE_DATA(SEQ INTEGER,DevCode INTEGER,ExtCode INTEGER,ExtNo INTEGER,IsLocal INTEGER,DevNoHigh INTEGER,DevNoLow INTEGER,BitNo INTEGER);
        CREATE TABLE COMMENT_DATA(DeviceSEQ INTEGER,CmtNo INTEGER,CmtData TEXT,DelFlag INTEGER);''')
        rows=[(1,32,0,0,0,0,10,0),(2,32,0,0,0,0,10,1),
              (3,40,0,0,0,0,10,0),(4,48,0,0,0,0,16,0),
              (5,35,208,42,0,0,10,0),(6,35,208,43,0,0,10,0),
              (7,32,0,0,1,0,10,0),(8,32,0,0,0,0,10,2)]
        con.executemany('INSERT INTO DEVICE_DATA VALUES(?,?,?,?,?,?,?,?)',rows)
        texts=[' word ','bit zero','file register','link register','unit A','unit B','local','deleted']
        con.executemany('INSERT INTO COMMENT_DATA VALUES(?,0,?,?)',[(i,t,1 if i==8 else 0) for i,t in enumerate(texts,1)])
        con.commit();con.close()
        printed=load_print_comments(root);base=load_comments_for_root(root)
        for name,text in [('D10',' word '),('D10.0','bit zero'),('ZR10','file register'),('W010','link register'),('U02A\\G10','unit A'),('U2B\\G10','unit B')]:
            assert comment_text_for(name,printed)==text,(name,printed)
        assert comment_text_for('D10.1',printed)==''
        assert comment_text_for('D10Z1',printed)==''
        assert base[('D',10)].all_text=='word'
        assert base[('ZR',10)].all_text=='file register'
        assert base[('W',16)].all_text=='link register'
        assert base[('U2AG',10)].all_text=='unit A'
        assert len(list(read_comment_records(db)))==7
        from gx3cli.gx3_semantic_diff import comment_map
        diff_comments=comment_map(root)
        assert diff_comments['D10.0']=='bit zero'
        assert diff_comments['U2A\\G10']=='unit A'
        assert diff_comments['D10']==' word '
        # All readers close SQLite before Windows removes the fixture.


if __name__=='__main__':
    test_word_bit_unit_and_deleted_comment_identity()
    print('comment identity checks passed')
