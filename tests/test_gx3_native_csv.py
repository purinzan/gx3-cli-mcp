"""Synthetic saved archive -> real CLI -> independently specified native CSV.

The byte fixtures describe the reader contract; no converter in the product
creates the instruction bytes that this test asserts against.
"""
import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile

from gx3cli.gx3_csv_export import export_csv
from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_native_csv import arguments, frames, mnemonic, validate_stack, native_comment_record
from gx3cli.gx3_synthetic_project import create_synthetic_project


def make_fixture(work):
    root=create_synthetic_project(work/'source')
    (root/'Config.xml').write_text('<Project><Config Unit="R08CPU" /></Project>',encoding='utf-8')
    db=sqlite3.connect(root/'001_LDDB.db');db.execute('delete from LadderBlocks')
    step=sqlite3.connect(root/'1_StepInfo.db')
    step.execute('create table T_Block(Pos real, BlockID text, StepSize integer, BodyID integer)')
    step.execute('create table T_Step(Pos real, ElementIDMacroFB integer, BlockID text, MilID integer, StepSize integer)')
    title='A:\t"試験😀"'
    title_bytes=title.encode('utf-16-le');units=len(title_bytes)//2
    title_data=f'V1:1:{units}:{title}:st{{st=c{{t=#:st=i}}:dim=0x1}}'
    title_frame=bytes([len(title_bytes)+5,0xc1,units+2,0])+title_bytes+bytes([len(title_bytes)+5])
    rung,size,_=generate_rung({'or':[{'and':[{'device':'M1'},{'device':'M2'}]},{'and':[{'device':'M3'},{'device':'M4'}]}]}, {'type':'coil','device':'Y10'})
    code=bytes.fromhex('0700010000ff07 05a0010105 0700010200ff07 05a0010205 0700010000ff07 05a0010305 0700010200ff07 05a0010405 0701010100ff07 0702010000ff07 05a0111005')
    note=bytes.fromhex('07c103024e0007') # N, a separately sized note record
    records=[]
    for index,(typ,data,row_size,payload,sizes) in enumerate([
        (1,title_data,1,title_frame,[units+2]),
        (0,rung,size,code+note,[1,1,1,1,1,1,3]),
        (5,'V1:0:end{type=end:dim=1x1}',1,bytes.fromhex('0702021500ff07'),[2]),
    ],1):
        guid=uuid.UUID(int=index);position=index*1024
        db.execute('insert into LadderBlocks values (?,?,?,?,?,?,?)',('_guid/'+str(guid),position,typ,data,row_size,1,0))
        block='{'+str(guid).upper()+'}'
        step.execute('insert into T_Block values (?,?,?,0)',(position,block,sum(sizes)))
        step.executemany('insert into T_Step values (?,0,?,?,?)',[(position,block,i,n) for i,n in enumerate(sizes)])
        records.append(struct.pack('<I',21+len(payload))+b'\x11'+guid.bytes_le+payload)
    db.commit();db.close();step.commit();step.close()
    pc=root/'ConvertData'/'1'/'PouPCode.pcode';pc.parent.mkdir(parents=True);pc.write_bytes(b''.join(records))
    return root,title,units+2


def rejected(call,fragment):
    try:call()
    except ValueError as error:assert fragment in str(error),str(error)
    else:raise AssertionError('invalid input accepted')


def test_archive_native_csv():
    with tempfile.TemporaryDirectory() as tmp:
        work=Path(tmp);root,title,start=make_fixture(work);archive=work/'demo.gx3'
        with zipfile.ZipFile(archive,'w') as stream:
            for p in root.rglob('*'):
                if p.is_file():stream.write(p,p.relative_to(root))
        before=hashlib.sha256(archive.read_bytes()).hexdigest()
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'PYTHONIOENCODING':'utf-8'}
        done=subprocess.run([sys.executable,'-m','gx3cli.gx3_cli','csv-export','--root',str(archive),'--kind','ladder','--output-dir',str(work/'csv')],cwd=work,env=env,capture_output=True,text=True,encoding='utf-8')
        assert done.returncode==0,done.stdout+done.stderr
        with (work/'csv'/'ladder_0001.csv').open(encoding='utf-16',newline='') as f:rows=list(csv.reader(f,delimiter='\t'))
        assert rows[:3]==[['demo'],['機種情報:','RCPU R08CPU'],['ステップ番号','行間ステートメント','命令','I/O(デバイス)','空欄','PIステートメント','ノート']]
        expected=[['0',title,'','','','','']]
        for index,(op,arg) in enumerate([('LD','M1'),('AND','M2'),('LD','M3'),('AND','M4'),('ORB',''),('OUT','Y10')]):
            expected.append([str(start+index),'',op,arg,'','',''])
        expected.extend([['','','','','','','N'],[str(start+9),'','END','','','','']])
        assert rows[3:]==expected
        manifest=json.loads((work/'csv'/'manifest.json').read_text())
        assert manifest['format']=='gx3-native-csv-v1'
        assert manifest['gxworks3_import_tested'] is False
        assert hashlib.sha256(archive.read_bytes()).hexdigest()==before


def test_saved_metadata_and_unknown_frames_fail_atomically():
    for mutation,reason in [('flags','unconverted'),('size','sizes differ'),('operand','inventory differs'),('opcode','unsupported instruction ID'),('truncated','frame')]:
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp);root,_,_=make_fixture(work)
            if mutation=='flags':
                con=sqlite3.connect(root/'001_LDDB.db');con.execute('update LadderBlocks set translated=0 where blocktype=0');con.commit();con.close()
            elif mutation=='size':
                con=sqlite3.connect(root/'1_StepInfo.db');con.execute('update T_Step set StepSize=99 where Pos=2048 and MilID=0');con.commit();con.close()
            else:
                pc=root/'ConvertData'/'1'/'PouPCode.pcode';data=pc.read_bytes()
                if mutation=='operand':data=data.replace(bytes.fromhex('05a0010105'),bytes.fromhex('05a0010905'))
                elif mutation=='opcode':data=data.replace(bytes.fromhex('0700010000ff07'),bytes.fromhex('077f010000ff07'),1)
                else:data=data.replace(bytes.fromhex('05a0010105'),bytes.fromhex('04a0010105'))
                pc.write_bytes(data)
            rejected(lambda:export_csv(root,work/'out',kind='ladder'),reason)
            assert not (work/'out').exists() and not list(work.glob('.gx3-csv-*'))


def test_decoder_boundaries_and_modifiers():
    def args(text):return arguments(list(frames(bytes.fromhex(text))))
    assert args('05a0b00105 05a0cb0005 05a0280c05')==['ZR12.0Z1']
    assert args('05a0cb0005 05a0b00105 05a0280c05')==['ZR12Z1.0']
    assert args('05a0d02a05 05a0b40a05 05a0231f05')==['U2A\\G31ZZ10']
    assert args('05a0cc0005 05a0b00205 05a0201e05')==['@D30Z2']
    assert args('05a0ca0405 05a0010105')==['K4M1']
    assert args('06a0a0ffff06 08a0a7ffffffff08 06a0af000006')==['K-1','H0FFFFFFFF','""']
    assert [mnemonic(bytes.fromhex(f'0701010{i}00ff07')) for i in range(5)]==['ANB','ORB','MPS','MRD','MPP']
    assert mnemonic(bytes.fromhex('07000202000207'))=='ANDP'
    assert mnemonic(bytes.fromhex('07000101000407'))=='LDFI'
    assert mnemonic(bytes.fromhex('0710032500ff07'))=='ANDD<='
    assert mnemonic(bytes.fromhex('07220300000207'))=='INCP'
    assert mnemonic(bytes.fromhex('1162090300ff49004e0050005500540011'))=='G.INPUT'
    rejected(lambda:args('05a0cb0005'),'without a value')
    rejected(lambda:list(frames(bytes.fromhex('05a0010104'))),'frame')
    rejected(lambda:args('05a0fe0105'),'unsupported operand type')
    rejected(lambda:mnemonic(bytes.fromhex('0710030600ff07')),'comparison')
    rejected(lambda:validate_stack([('AND',['M1'])]),'without an accumulator')
    rejected(lambda:validate_stack([('MPP',[])]),'branch stack')
    rejected(lambda:validate_stack([('LD',['M1']),('MPS',[])]),'unbalanced')
    validate_stack([('LD',['M1']),('MPS',[]),('MRD',[]),('MPP',[])])


def test_native_comment_order_and_hex_spelling():
    records=[('XA',None,{5:'ten'}),('M7',None,{5:'seven'}),('M6',None,{5:'six'}),('UAF\\G31.0',None,{5:'bit'})]
    result=sorted(map(native_comment_record,records))
    assert [r[1] for r in result]==['M6','M7','X0A','U0AF\\G31.0']
    with tempfile.TemporaryDirectory() as tmp:
        root,_,_=make_fixture(Path(tmp))
        out=Path(tmp)/'comments'
        export_csv(root,out,kind='comments')
        content=(out/'COMMENT.csv').read_bytes().decode('utf-16')
        assert '\r\n' not in content and '\n' in content



if __name__=='__main__':
    test_archive_native_csv()
    test_saved_metadata_and_unknown_frames_fail_atomically()
    test_decoder_boundaries_and_modifiers()
    test_native_comment_order_and_hex_spelling()
    print('native CSV checks passed')
