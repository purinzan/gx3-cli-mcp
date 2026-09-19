from __future__ import annotations

"""Export saved GX Works3 instruction CSV or decoded analysis tables."""
import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import xml.etree.ElementTree as ET
import tempfile
from collections import defaultdict
from pathlib import Path

from gx3cli.gx3_comment_store import read_comment_records, preferred_text
from gx3cli.gx3_format import build_format_inventory
from gx3cli.gx3_input_identity import fingerprint
from gx3cli.gx3_intermediate_tool import read_ladder_rows, find_stepinfo_map
from gx3cli.gx3_native_csv import export_program, cpu_heading, COLUMNS, native_comment_record
from gx3cli.gx3_label_probe import build_probe
from gx3cli.gx3_label_resolve import load_label_resolver
from gx3cli.gx3_ladder_logic import row_logic_analysis, logic_to_text
from gx3cli.gx3_ladder_print import parse_rung, parse_pointers
from gx3cli.gx3_program_map import load_program_map
from gx3cli.gx3_project_paths import resolve_project_root, find_comment_db
from gx3cli.review_gx3_project import load_rows, extract_title


LADDER_COLUMNS = ['program', 'lddb', 'block_pos', 'block_step', 'x', 'y',
                  'kind', 'opcode', 'contact_type', 'operands_json', 'title',
                  'input_condition', 'condition_json', 'decode_status']
LIMITS = [
    'Ladder files contain decoded drawing elements, not GX Works3 importable instruction lists.',
    'block_step is a block start, not an instruction step. Blank means unavailable.',
    'MPS/MRD/MPP/ANB/ORB execution order and per-instruction steps are not reconstructed.',
    'Conditions are static evidence; jump/loop execution, runtime addresses and previous scans are not simulated.',
    'COMMENT.csv follows the UTF-16 tab-delimited comment layout; GX Works3 import has not been executed.',
    'Unknown/local/high-address comment records and non-LD programs are listed as exclusions.',
    'Label rows are decoded metadata, not a reconstructed GX Works3 Global.csv.',
]


NATIVE_LIMITS = [
    'Ladder CSV contains the saved RCPU instruction sequence in GX Works3 CSV layout.',
    'Saved instruction sizes, block identities, conversion flags and drawing operand inventories are checked.',
    'This exports the saved conversion data; it does not compile drawings or prove compiled-cache freshness from wiring.',
    'GX Works3 import and PLC execution have not been performed.',
    'Unknown encodings or inconsistent metadata stop the native export without publishing a partial program.',
    'COMMENT.csv uses the GX Works3 comment layout; label metadata tables are auxiliary, not Global.csv.',
]


def write_rows(path, rows, *, lineterminator='\r\n'):
    with path.open('w', encoding='utf-16', newline='') as stream:
        writer=csv.writer(stream, delimiter='\t', quoting=csv.QUOTE_ALL, lineterminator=lineterminator)
        writer.writerows(rows)


def write_table(path, rows, columns):
    write_rows(path, [columns, *[[row.get(c, '') for c in columns] for row in rows]])


def comment_count(path):
    import sqlite3
    con=sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    try:return con.execute('select count(*) from DEVICE_DATA').fetchone()[0]
    finally:con.close()


def export_csv(source: Path, destination: Path, *, kind='all', program='', project_name='', format='gxworks3'):
    if kind not in {'all','ladder','comments','labels'}:raise ValueError('unknown export kind')
    if program and kind not in {'all','ladder'}:raise ValueError('program selector requires ladder or all')
    if format not in {'gxworks3','analysis'}:raise ValueError('unknown CSV format')
    source=source.resolve();destination=destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError(f'output already exists: {destination}; choose a new directory')
    archive_hash=hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    root=resolve_project_root(source)
    if destination.resolve().is_relative_to(root.resolve()):
        raise ValueError('output must be outside the extracted project')
    before=fingerprint(root)
    inventory=build_format_inventory(root)
    if not inventory.has_known_program_db and not inventory.dc_count:
        raise ValueError('no supported project data found')
    name=project_name or source.stem
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.gx3-csv-',dir=destination.parent))
    issues=[];files=[]
    def table(filename, rows, columns, **metadata):
        write_table(stage/filename,rows,columns)
        files.append({'file':filename,'rows':len(rows),**metadata})
    try:
        if kind in {'all','ladder'}:
            pm=load_program_map(root);labels=load_label_resolver(root)
            groups=defaultdict(list)
            # Include empty LD databases in the inventory as well.
            raw_rows=read_ladder_rows(root)
            for lddb in raw_rows:groups[lddb]=[]
            for row in load_rows(root,{},rows_by_db=raw_rows):groups[row.lddb].append(row)
            selected=[db for db in sorted(groups) if not program or program in {pm.label(db),db}]
            if program and not selected:raise ValueError(f'program not found: {program}')
            if not selected and format=='gxworks3':raise ValueError('no LD programs to export')
            if not selected:issues.append({'scope':'ladder','location':'','reason':'no LD programs to export'})
            stepinfo=find_stepinfo_map(root,raw_rows) if format=='gxworks3' else {}
            heading=cpu_heading(root) if format=='gxworks3' else ''
            sections=[];pointers=[];wiring=[]
            for number,db in enumerate(selected,1):
                decoded=[];label=pm.label(db)
                if format=='gxworks3':
                    try:
                        native,count=export_program(root,db,raw_rows[db],{r.pos:r for r in groups[db]},labels,stepinfo.get(db))
                    except (ValueError,OSError,sqlite3.Error,ET.ParseError) as error:
                        raise ValueError(f'native ladder export failed for {label}: {error}') from error
                    filename=f'ladder_{number:04d}.csv'
                    write_rows(stage/filename,[[name],['機種情報:',heading],COLUMNS,*native])
                    files.append({'file':filename,'rows':len(native),'instructions':count,'program':label,'lddb':db,'format':'gxworks3-ladder'})
                    continue
                for raw in raw_rows[db]:
                    if int(raw['blocktype']) in {1,2}:
                        sections.append({'program':label,'lddb':db,'block_pos':raw['pos'],
                                         'text':extract_title(str(raw['data']))})
                for row in sorted(groups[db],key=lambda r:r.pos):
                    ops,vertical,horizontal=parse_rung(row,labels)
                    status='decoded'
                    if len(ops)!=row.data.count('s=ce{') or any('?' in op.operands for op in ops):
                        status='partial'
                        issues.append({'scope':db,'location':row.pos,'reason':'unparsed or unknown ladder elements'})
                    analysis=row_logic_analysis(row,labels)
                    for op in ops:
                        condition=analysis.output_logic.get((op.x,op.y))
                        if condition is not None and condition.get('op')=='too_large':
                            issues.append({'scope':db,'location':row.pos,'reason':'condition expression limit'})
                        decoded.append(dict(zip(LADDER_COLUMNS,[label,db,row.pos,pm.step_of(db,row.pos),op.x,op.y,
                            'contact' if op.is_contact else 'condition' if op.is_inline_box else 'output',
                            ('CONTACT_A' if op.role=='a' else 'CONTACT_B') if op.is_contact else 'OUT' if op.is_coil else op.opcode_text(),
                            op.ct_code,json.dumps(op.operands,ensure_ascii=False),row.title,
                            logic_to_text(condition) if condition is not None else '',
                            json.dumps(condition,ensure_ascii=False) if condition is not None else '',status])))
                    for n,y in parse_pointers(row.data):pointers.append({'program':label,'lddb':db,'block_pos':row.pos,'y':y,'pointer':f'P{n}'})
                    for x,y in vertical:wiring.append({'program':label,'lddb':db,'block_pos':row.pos,'kind':'vertical','x':x,'y':y,'end_x':''})
                    for x,y,end in horizontal:wiring.append({'program':label,'lddb':db,'block_pos':row.pos,'kind':'horizontal','x':x,'y':y,'end_x':end})
                table(f'ladder_{number:04d}.csv',decoded,LADDER_COLUMNS,program=label,lddb=db)
            if format=='analysis':
                table('statements.csv',sections,['program','lddb','block_pos','text'])
                table('pointers.csv',pointers,['program','lddb','block_pos','y','pointer'])
                table('wiring.csv',wiring,['program','lddb','block_pos','kind','x','y','end_x'])
            for suffix in ('*_FBDDB.db','*_STDB.db'):
                for path in root.glob(suffix):issues.append({'scope':path.name,'location':'','reason':'non-LD program not exported'})
        if kind in {'all','comments'}:
            db=find_comment_db(root)
            if db is None:
                issues.append({'scope':'comments','location':'','reason':'comment database absent'})
            else:
                records=list(read_comment_records(db))
                display_records=sorted(map(native_comment_record,records),key=lambda r:r[0]) if format=='gxworks3' else [(None,n,t) for n,_,t in records]
                comment_rows=[[n,preferred_text(t)] for _,n,t in display_records if preferred_text(t)]
                write_rows(stage/'COMMENT.csv',[[name],['デバイス名','コメント'],*comment_rows],lineterminator='\n' if format=='gxworks3' else '\r\n')
                files.append({'file':'COMMENT.csv','rows':len(comment_rows),'format':'gxworks3-comment-layout','blank_records_omitted':len(records)-len(comment_rows)})
                excluded=comment_count(db)-len(records)
                if excluded:issues.append({'scope':'comments','location':'','reason':f'{excluded} unsupported identity records excluded'})
                table('comment_languages.csv',[{'device':n,'language_id':lang,'text':text} for n,_,texts in records for lang,text in texts.items()],['device','language_id','text'])
        if kind in {'all','labels'}:
            if not (root/'LabelData.db').exists():
                issues.append({'scope':'labels','location':'','reason':'label database absent'})
            else:
                probe=build_probe(root)
                for key in ('summary_rows','label_rows','comment_rows','assign_rows','array_rows'):
                    rows=probe[key];columns=list(dict.fromkeys(k for row in rows for k in row))
                    table('labels_'+key+'.csv',rows,columns or ['no_records'])
        if fingerprint(root)!=before or (archive_hash and hashlib.sha256(source.read_bytes()).hexdigest()!=archive_hash):
            raise ValueError('source changed during export; no output published')
        table('issues.csv',issues,['scope','location','reason'])
        limits=NATIVE_LIMITS if format=='gxworks3' else LIMITS
        manifest={'format':'gx3-native-csv-v1' if format=='gxworks3' else 'gx3-analysis-csv-v1','project':name,'source_sha256':archive_hash or before,
                  'selection':{'kind':kind,'program':program},'ladder_importable':None if format=='gxworks3' else False,'gxworks3_import_tested':False,
                  'status':'partial' if issues else 'exported','issues':issues,'limits':limits,'files':files}
        (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        (stage/'README.txt').write_text('\n'.join(limits)+'\n',encoding='utf-8')
        if destination.exists() or destination.is_symlink():raise ValueError('output was created by another process')
        os.rename(stage,destination)
        return manifest
    finally:
        if stage.exists():shutil.rmtree(stage)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,help='GX3 file or extracted project')
    parser.add_argument('--output-dir',required=True,help='new directory outside the extracted project')
    parser.add_argument('--kind',choices=['all','ladder','comments','labels'],default='all')
    parser.add_argument('--format',choices=['gxworks3','analysis'],default='gxworks3',help='native saved instruction CSV (default), or analysis tables')
    parser.add_argument('--program',default='',help='exact POU name or LDDB filename (ladder/all only)')
    parser.add_argument('--project-name',default='',help='project heading for GX Works3 CSV files')
    args=parser.parse_args(argv)
    if args.program and args.kind not in {'all','ladder'}:parser.error('--program requires ladder or all')
    try:
        result=export_csv(Path(args.root),Path(args.output_dir),kind=args.kind,program=args.program,project_name=args.project_name,format=args.format)
    except (ValueError,OSError,sqlite3.Error,ET.ParseError) as error:
        parser.exit(2,f'CSV export failed: {error}\n')
    print(f"CSV exported: {args.output_dir} ({result['status']}; {len(result['files'])} files)")
    print('Saved GX Works3 instruction CSV; import has not been executed.' if args.format=='gxworks3' else 'Ladder CSV contains analysis tables, not an instruction list.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
