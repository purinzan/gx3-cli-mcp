from __future__ import annotations

"""Read saved RCPU instruction records. Unknown encodings fail closed.

The framed POU stream supplies execution order; LDDB supplies a separate
instruction inventory check. StepInfo must agree with every sized record.
No Boolean expression is compiled back into executable instructions.
"""
from collections import Counter, defaultdict
import re
import xml.etree.ElementTree as ET

from gx3cli.gx3_device_name import device_radix, parse_device_name
from gx3cli.gx3_comment_store import DEVICE_CODE_BY_TYPE
from gx3cli.gx3_intermediate_tool import (
    find_stepinfo_map, normalize_guid, pcode_path_for_stepinfo,
    pcode_record_for_guid, read_stepinfo_blocks,
)
from gx3cli.gx3_ladder_print import parse_rung, parse_pointers
from gx3cli.review_gx3_project import extract_title

COLUMNS = ['ステップ番号', '行間ステートメント', '命令', 'I/O(デバイス)',
           '空欄', 'PIステートメント', 'ノート']
# Group and operation IDs; the intervening byte is the saved instruction size.
OPS = {
    0x01: {0:'ANB',1:'ORB',2:'MPS',3:'MRD',4:'MPP'},
    0x02: {0:'OUT',1:'OUT',2:'OUTH',3:'SET',4:'RST',5:'PLS',6:'PLF',21:'END'},
    0x03: {0:'POINTER',1:'NOPLF'},
    0x21: {0:'+',1:'+',2:'-',3:'-',5:'D+',7:'D-',8:'*',10:'/',12:'D*',14:'D/',35:'$+'},
    0x22: {0:'INC',1:'DINC'},
    0x23: {14:'NEG',15:'DNEG',42:'INT2DINT',47:'DINT2INT'},
    0x24: {0:'MOV',1:'DMOV',3:'$MOV',4:'CML',6:'BMOV',7:'FMOV',11:'SWAP',14:'DFMOV',18:'FMOVL',19:'DFMOVL'},
    0x25: {0:'CJ',6:'GOEND'},
    0x27: {0:'WAND',2:'WOR'},
    0x29: {13:'SFTBL'},
    0x2a: {0:'BSET',1:'BRST',4:'BKRST'},
    0x2b: {2:'SUM',4:'DECO',5:'ENCO',11:'WTOB',12:'BTOW',13:'MAX',15:'MIN',19:'WSUM'},
    0x2e: {0:'FROM',1:'DFROM',2:'TO',3:'DTO'},
    0x31: {0:'BINDA',6:'DABIN',7:'DDABIN',13:'LEN',24:'MIDR',26:'INSTR',37:'STRDEL'},
    0x35: {0:'DATERD',23:'DATE2SEC'},
    0x39: {4:'ADRSET'},
    0x52: {0:'FOR',1:'NEXT'},
}
DEVICES = {1:'M',2:'SM',3:'L',16:'X',17:'Y',20:'B',22:'DX',
           32:'D',33:'SD',35:'G',40:'ZR',48:'W',66:'T',96:'Z',101:'P',112:'U'}
MODIFIERS = {0xb0:'Z',0xb4:'ZZ',0xca:'digit',0xcb:'bit',0xcc:'indirect',0xd0:'unit'}


def frames(data):
    offset = 0
    while offset < len(data):
        size = data[offset]
        if size < 3 or offset + size > len(data) or data[offset+size-1] != size:
            raise ValueError('unsupported or truncated PCode frame')
        yield data[offset:offset+size]
        offset += size


def mnemonic(frame):
    if len(frame) < 7:
        raise ValueError('short instruction header')
    group, size, code, flags, pulse = frame[1:6]
    if not size or pulse not in (255,2,4):
        raise ValueError('unsupported instruction size or pulse encoding')
    if group in (0x61,0x62):
        if flags or pulse == 4:
            raise ValueError('unsupported named instruction flags')
        name = frame[6:-1].decode('utf-16-le')
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*',name):
            raise ValueError('unsupported named instruction name')
        return ('Z' if group == 0x61 else 'G') + ('P' if pulse == 2 else '') + '.' + name
    if len(frame) != 7:
        raise ValueError('unsupported instruction header length')
    if group == 0:
        if flags:raise ValueError('unsupported contact flags')
        if code in (8,9,10):
            expected = {8:255,9:2,10:4}[code]
            if pulse != expected:raise ValueError('invalid expression pulse')
            return {8:'INV',9:'MEP',10:'MEF'}[code]
        if code > 5:raise ValueError('unknown contact opcode')
        base = ('LD','LD','AND','AND','OR','OR')[code]
        suffix = {255:'',2:'P',4:'F'}[pulse] + ('I' if code%2 else '')
        return 'ANI' if base+suffix == 'ANDI' else base+suffix
    if group == 0x10:
        if flags or pulse != 255:raise ValueError('unsupported comparison flags')
        family, rest = divmod(code,24)
        if family not in (0,1,4):raise ValueError('unknown comparison family')
        prefix, comparison = divmod(rest,8)
        if prefix > 2 or comparison > 5:raise ValueError('unknown comparison')
        return ('LD','AND','OR')[prefix] + {0:'',1:'D',4:'$'}[family] + ('=','<>','>','>=','<','<=')[comparison]
    try:op = OPS[group][code]
    except KeyError:raise ValueError(f'unsupported instruction ID {group:02x}/{code:02x}') from None
    if flags:
        if flags != 2 or op not in {'WSUM','DATE2SEC'}:raise ValueError('unsupported instruction flags')
        op += '_U'
    if op in {'PLS','PLF'}:
        if pulse != (2 if op == 'PLS' else 4):raise ValueError('invalid coil pulse')
    elif pulse == 2:op += 'P'
    elif pulse != 255:raise ValueError('unsupported instruction pulse')
    return op


def hex_number(value):
    text = f'{value:X}'
    return ('0' if text[0] in 'ABCDEF' else '') + text


def operand(frame):
    if len(frame) < 5 or frame[1] != 0xa0:
        raise ValueError('unsupported operand frame')
    code = frame[2]; payload = frame[3:-1]
    value = int.from_bytes(payload,'little')
    if code in MODIFIERS:
        return MODIFIERS[code], value
    if code == 0xaf:
        return 'value', '"' + ('' if payload == b'\0\0' else payload.decode('utf-16-le')) + '"'
    if len(payload) > 4:raise ValueError('unsupported numeric operand width')
    if code in DEVICES:
        prefix = DEVICES[code]
        return 'value', prefix + (hex_number(value) if (prefix == 'U' or device_radix(prefix) == 16) else str(value))
    if code in (0xa0,0xa1,0xa2,0xa3):
        width = 32 if code == 0xa2 else 16
        if value >= 1 << width:raise ValueError('constant outside encoded width')
        if value & (1 << (width-1)):value -= 1 << width
        return 'value', 'K'+str(value)
    if code in (0xa6,0xa7):return 'value','H'+hex_number(value)
    raise ValueError(f'unsupported operand type {code:02x}')


def arguments(chunks):
    result=[]; modifiers=[]
    for chunk in chunks:
        kind,value = operand(chunk)
        if kind != 'value':
            modifiers.append((kind,value));continue
        for kind,number in reversed(modifiers):
            if kind in ('Z','ZZ'):value += kind+str(number)
            elif kind == 'bit':value += f'.{number:X}'
            elif kind == 'digit':value = f'K{number}'+value
            elif kind == 'indirect':
                if number:raise ValueError('unsupported indirect modifier')
                value = '@'+value
            elif kind == 'unit':value = 'U'+hex_number(number)+'\\'+value
        result.append(value);modifiers=[]
    if modifiers:raise ValueError('operand modifier without a value')
    return result


def native_comment_record(record):
    name,_,texts=record
    match=re.fullmatch(r'U([0-9A-F]+)\\G(\d+)(?:\.([0-9A-F]+))?',name)
    if match:
        unit,address,bit=match.groups();unit=int(unit,16);address=int(address)
        display='U'+hex_number(unit)+'\\G'+str(address)
        order=(256,unit,address,-1 if bit is None else int(bit,16))
    else:
        base,separator,bit=name.partition('.')
        prefix,address=parse_device_name(base)
        display=prefix+(hex_number(address) if device_radix(prefix)==16 else str(address))
        order=(DEVICE_CODE_BY_TYPE[prefix],0,address,int(bit,16) if separator else -1)
    if bit is not None and bit!='':display+='.'+bit
    return order,display,texts


def normal_arg(text):
    if text.startswith('"'):return text
    if re.fullmatch(r'#P\d+',text):return text[1:]
    return re.sub(r'^(H|DX|DY|X|Y|B|W|SB|SW|U)0+([0-9A-F]+)',lambda m:m[1]+m[2],text)


def instruction_key(op,args):
    for prefix in ('AND','LD','OR'):
        if op.startswith(prefix):
            suffix=op[len(prefix):]
            return (('CONTACT:' if suffix in ('','I','P','F','PI','FI') else 'PRED:')+suffix,tuple(map(normal_arg,args)))
    return ('CONTACT:I' if op == 'ANI' else op,tuple(map(normal_arg,args)))


def drawing_keys(row,labels):
    ops,_,_=parse_rung(row,labels)
    if len(ops) != row.data.count('s=ce{'):
        raise ValueError('drawing contains unparsed elements')
    keys=[]
    for op in sorted(ops,key=lambda o:(o.y,o.x)):
        if '?' in op.operands:raise ValueError('drawing has unresolved operands')
        if op.is_contact:
            if op.ct_code not in ('a','p','f'):raise ValueError('unknown drawing contact type')
            code='CONTACT:'+{'a':'','p':'P','f':'F'}[op.ct_code]+('I' if op.role=='b' else '')
        elif op.is_coil:code='OUT'
        elif op.role == 'ME':code={'p':'MEP','f':'MEF'}.get(op.ct_code,'?')
        else:code=('PRED:' if op.is_inline_box and op.role != 'INV' else '')+op.opcode_text()
        keys.append((code,tuple(map(normal_arg,op.operands))))
    return keys


def validate_stack(instructions):
    depth=0;saved=0
    for op,args in instructions:
        if op.startswith('LD'):depth+=1
        elif op in {'ANB','ORB'}:
            if depth<2:raise ValueError('invalid expression stack')
            depth-=1
        elif op.startswith(('AND','OR')) or op in {'ANI','INV','MEP','MEF'}:
            if not depth:raise ValueError('condition operation without an accumulator')
        elif op=='MPS':
            if not depth:raise ValueError('MPS without a condition')
            saved+=1
        elif op in {'MRD','MPP'}:
            if not saved:raise ValueError('invalid branch stack')
            if op=='MPP':saved-=1
    if saved:raise ValueError('unbalanced branch stack')


def cpu_heading(root):
    unit = ET.parse(root/'Config.xml').getroot().find('Config')
    model = unit.get('Unit','') if unit is not None else ''
    # This decoder currently supports the saved RCPU encoding only.
    if not re.fullmatch(r'R\d+[A-Z0-9]*',model):
        raise ValueError('native export requires a supported RCPU configuration')
    return 'RCPU '+model


def export_program(root,db,raw_rows,drawings,labels,stepinfo):
    if not stepinfo:raise ValueError('saved StepInfo is absent')
    blocks,steps=read_stepinfo_blocks(root,stepinfo)
    by_guid=defaultdict(list)
    for step in steps:by_guid[step['guid']].append(step)
    known={b['guid'] for b in blocks}
    if len(known)!=len(raw_rows) or len({r['pos'] for r in raw_rows})!=len(raw_rows):
        raise ValueError('duplicate block identities or positions')
    if set(by_guid)-known:raise ValueError('StepInfo contains orphan instructions')
    if known != {normalize_guid(r['id']) for r in raw_rows}:
        raise ValueError('StepInfo and drawing block identities differ')
    pcode=pcode_path_for_stepinfo(root,stepinfo).read_bytes()
    result=[];step_number=0;instruction_count=0
    for raw in sorted(raw_rows,key=lambda r:r['pos']):
        if raw['translated'] != 1 or raw['ConvTarget'] != 0:
            raise ValueError('drawing has unconverted changes')
        guid=normalize_guid(raw['id']);record=pcode_record_for_guid(pcode,guid)
        if record is None:raise ValueError('saved PCode block is absent')
        sizes=by_guid[guid]
        if any(s['pos'] != raw['pos'] for s in sizes):raise ValueError('StepInfo block position differs')
        sizes=sorted(sizes,key=lambda s:s['mil_id'])
        if [s['mil_id'] for s in sizes] != list(range(len(sizes))):raise ValueError('StepInfo instruction IDs are not contiguous')
        chunks=list(frames(record[21:]));seen_sizes=[];instructions=[]
        if raw['blocktype'] in (1,2):
            text=extract_title(raw['data'])
            separate=':st=s}' in raw['data']
            if not chunks or any(c[1] not in (0xc0,0xc1) or c[3] != 0 for c in chunks):
                raise ValueError('unsupported statement encoding')
            compiled=b''.join(c[4:-1] for c in chunks).decode('utf-16-le')
            if compiled != text and not (not compiled and not text.strip()):
                raise ValueError('compiled statement differs from drawing')
            if sum(c[2] for c in chunks) != sum(s['step_size'] for s in sizes):
                raise ValueError('statement and StepInfo sizes differ')
            if separate:text='*'+text
            result.append([str(step_number),text.replace('\r\n',r'\r\n'),'','','','',''])
            step_number+=sum(s['step_size'] for s in sizes)
            continue
        if raw['blocktype'] not in (0,5):raise ValueError('unsupported ladder block type')
        current=None;operand_chunks=[]
        def flush():
            nonlocal current,operand_chunks,instruction_count
            if current is None:
                if operand_chunks:raise ValueError('operands without instruction')
                return
            opcode,start=current;args=arguments(operand_chunks)
            if opcode == 'POINTER':
                if len(args)!=1 or not re.fullmatch(r'P\d+',args[0]):raise ValueError('invalid pointer definition')
                opcode=args[0];args=[]
            if opcode in {'MPS','MRD','MPP','ANB','ORB','INV','MEP','MEF','END','NEXT','GOEND','NOPLF'} and args:
                raise ValueError('unexpected operands for a zero-argument instruction')
            instructions.append((opcode,args))
            result.append([str(start),'',opcode,args[0] if args else '','','',''])
            result.extend([['','','',arg,'','',''] for arg in args[1:]])
            instruction_count+=1;current=None;operand_chunks=[]
        for chunk in chunks:
            if chunk[1] < 0x80:
                flush();current=(mnemonic(chunk),step_number)
                seen_sizes.append(chunk[2]);step_number+=chunk[2]
            elif chunk[1] == 0xa0:operand_chunks.append(chunk)
            elif chunk[1] in (0xc0,0xc1):
                flush()
                if chunk[3] != 2:raise ValueError('unsupported inline statement encoding')
                result.append(['','','','','','',chunk[4:-1].decode('utf-16-le')])
                seen_sizes.append(chunk[2]);step_number+=chunk[2]
            else:raise ValueError('unknown PCode frame kind')
        flush()
        if seen_sizes != [s['step_size'] for s in sizes]:raise ValueError('PCode and StepInfo instruction sizes differ')
        if raw['blocktype'] == 5:
            if instructions != [('END',[])]:raise ValueError('invalid END block')
        else:
            row=drawings.get(raw['pos'])
            if row is None:raise ValueError('drawing row absent')
            structural={'MPS','MRD','MPP','ANB','ORB'}
            compiled=[instruction_key(op,args) for op,args in instructions if op not in structural and not re.fullmatch(r'P\d+',op)]
            drawn=drawing_keys(row,labels)
            if Counter(compiled) != Counter(drawn):
                raise ValueError('saved instruction inventory differs from drawing')
            outputs=lambda keys:[k for k in keys if not k[0].startswith(('CONTACT:','PRED:')) and k[0] not in {'INV','MEP','MEF'}]
            if outputs(compiled) != outputs(drawn):raise ValueError('saved output order differs from drawing')
            validate_stack(instructions)
            if Counter(op for op,args in instructions if re.fullmatch(r'P\d+',op)) != Counter(f'P{n}' for n,y in parse_pointers(row.data)):
                raise ValueError('saved pointer definitions differ from drawing')
    if not result or result[-1][2] != 'END':raise ValueError('program has no terminal END')
    return result,instruction_count
