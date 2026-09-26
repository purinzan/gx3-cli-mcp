"""GX Works listed-instruction CSV: decode records, then fold instruction logic.

The block stack (LD/ANB/ORB) and saved-result stack (MPS/MRD/MPP) are
independent. No GX3 binary layout is inferred from CSV coordinates.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from gx3cli.gx3_arg_decode import base_opcode, write_indices


def normalize(value: str) -> str:
    return re.sub(r'[\s_.()（）/\-]', '', value.lstrip('\ufeff').lower())


INSTRUCTION = ('instruction', 'mnemonic', 'opcode', '命令', '命令語')
OPERAND = ('operand', 'operands', 'device', 'devices', 'argument', 'arguments', 'オペランド', 'デバイス', 'I/O (Device)', 'I/O(デバイス)')
STEP = ('step', 'step no.', 'stepno', 'pos', 'position', 'ステップ', 'ステップ番号', 'ステップNo.')
TITLE = ('title', 'section', 'line statement', 'タイトル', '行間ステートメント')
PROGRAM = ('lddb', 'program', 'pou', 'file', 'プログラム')
DATA = ('data', 'ladder_data', 'block_data', 'rung_data', 'serialized', 'serialized_data', 'body')


def field(row: dict[str, str], names: tuple[str, ...]) -> str:
    normalized = {normalize(k): v for k, v in row.items()}
    return next((normalized[normalize(n)].strip() for n in names
                 if normalized.get(normalize(n), '').strip()), '')


def read_csv_records(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = raw.decode('utf-16')
    else:
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp932')
    # Mitsubishi's .csv is tab-delimited, with two metadata records before
    # the header. Also accept conventional comma-delimited exports.
    for delimiter in ('\t', ','):
        records = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        for index, record in enumerate(records):
            headers = {normalize(cell) for cell in record}
            if headers.intersection(map(normalize, INSTRUCTION + DATA)):
                names = [normalize(cell) for cell in record if cell.strip()]
                if len(names) != len(set(names)):
                    raise ValueError('CSV contains duplicate column names')
                result = []
                for line, row in enumerate(records[index + 1:], index + 2):
                    if not any(row):
                        continue
                    if any(row[len(record):]):
                        raise ValueError(f'CSV row {line}: values outside header columns')
                    result.append(dict(zip(record, row + [''] * max(0, len(record) - len(row)))))
                return result
    raise ValueError('CSV instruction/data header not found')


def operands(text: str) -> tuple[str, ...]:
    # Quoted PLC string constants remain a single operand, including spaces.
    pattern = re.compile(r'"(?:[^"\\]|\\.|"")*"|[^\s,"]+')
    result = []
    end = 0
    for match in pattern.finditer(text):
        if text[end:match.start()].strip(' \t\r\n,'):
            raise ValueError('CSV operand has malformed quoting')
        result.append(match[0])
        end = match.end()
    if text[end:].strip(' \t\r\n,'):
        raise ValueError('CSV operand has malformed quoting')
    return tuple(result)



@dataclass(frozen=True)
class Instruction:
    program: str
    step: int
    title: str
    opcode: str
    operands: tuple[str, ...]


@dataclass(frozen=True)
class CsvOutput:
    instruction: Instruction
    condition: str
    device: str
    diagnostic: str = ''


def decode_instructions(rows: list[dict[str, str]], program: str) -> list[Instruction]:
    result: list[Instruction] = []
    title = ''
    for index, row in enumerate(rows, 1):
        title = field(row, TITLE) or title
        step = field(row, STEP)
        if '[Title]' in step:
            title = step.split('[Title]', 1)[1].strip()
        op = field(row, INSTRUCTION)
        args = operands(field(row, OPERAND))
        if not op:
            if args:
                if not result:
                    raise ValueError(f'CSV row {index}: operand without instruction')
                last = result[-1]
                result[-1] = Instruction(last.program, last.step, last.title,
                                        last.opcode, last.operands + args)
            continue
        parts = op.split(maxsplit=1)
        op = parts[0].upper()
        if len(parts) > 1:
            args = operands(parts[1]) + args
        match = re.match(r'^\d+', step)
        result.append(Instruction(field(row, PROGRAM) or program,
                                  int(match[0]) if match else index, title, op, args))
    return result


def combine(left: str, op: str, right: str) -> str:
    if op == 'AND':
        left = f'({left})' if ' OR ' in left else left
        right = f'({right})' if ' OR ' in right else right
    return f'{left} {op} {right}'


def parse_instructions(instructions: list[Instruction]) -> list[CsvOutput]:
    out: list[CsvOutput] = []
    current = ''
    blocks: list[str] = []
    saved: list[str] = []
    after_output = False
    program = ''
    uncertainty = ''

    def fail(inst: Instruction, message: str) -> None:
        raise ValueError(f'{inst.program}:{inst.step}: {message}')

    for inst in instructions:
        op, args = inst.opcode, inst.operands
        if op in {'ANB', 'ORB', 'MPS', 'MRD', 'MPP', 'INV', 'MEP', 'MEF', 'END', 'FEND', 'NOP'} and args:
            fail(inst, f'{op} does not take operands')
        if inst.program != program:
            if blocks or saved:
                fail(inst, 'unbalanced logic stack at program boundary')
            current, uncertainty, after_output = '', '', False
            program = inst.program
        contact = re.fullmatch(r'(LD|AND|OR)(I|PI|FI|P|F)?', 'ANDI' if op == 'ANI' else op)
        comparison = re.fullmatch(r'(LD|AND|OR)(D|E|ED)?(=|<>|<=|>=|<|>)(_U)?', op)
        if contact or comparison:
            mode = (contact or comparison)[1]
            needed = 1 if contact else 2
            if len(args) != needed:
                fail(inst, f'{op} requires {needed} operand(s)')
            if contact:
                suffix = contact[2] or ''
                term = args[0]
                if 'P' in suffix:
                    term = f'RISE({term})'
                elif 'F' in suffix:
                    term = f'FALL({term})'
                if suffix.endswith('I'):
                    term = f'/{term}'
            else:
                term = f'{op[len(mode):]}({", ".join(args)})'
            if mode == 'LD':
                if current and not after_output:
                    blocks.append(current)
                elif after_output and blocks:
                    fail(inst, 'uncombined logic blocks before new rung')
                current = term
            else:
                if not current:
                    fail(inst, f'{op} without initial LD')
                current = combine(current, mode, term)
            after_output = False
        elif op in {'ANB', 'ORB'}:
            if not blocks or not current:
                fail(inst, f'{op} without two logic blocks')
            current = combine(blocks.pop(), 'AND' if op == 'ANB' else 'OR', current)
            after_output = False
        elif op in {'MPS', 'MRD', 'MPP'}:
            if op == 'MPS':
                if not current:
                    fail(inst, 'MPS without condition')
                saved.append(current)
            else:
                if not saved:
                    fail(inst, f'{op} without MPS')
                current = saved.pop() if op == 'MPP' else saved[-1]
            after_output = False
        elif op in {'INV', 'MEP', 'MEF'}:
            if not current:
                fail(inst, f'{op} without condition')
            current = f'{dict(INV="NOT", MEP="RISE", MEF="FALL")[op]}({current})'
            after_output = False
        elif op in {'END', 'FEND'}:
            if blocks or saved:
                fail(inst, 'unbalanced logic stack at END/FEND')
            current, after_output = '', False
        elif op == 'NOP':
            continue
        else:
            indices, _ = write_indices(op, len(args))
            if op in {'OUT', 'OUTH', 'SET', 'RST', 'PLS', 'PLF', 'FF', 'SFT', 'SFTP'}:
                indices = {0}
            if op in {'MC', 'MCR', 'CALL', 'CALLP', 'CJ', 'SCJ', 'JMP', 'RET', 'IRET', 'FOR', 'NEXT'}:
                uncertainty = uncertainty or f'unsupported control flow {op} at step {inst.step}'
            # Validate common fixed-arity instructions before consulting a
            # permissive legacy destination rule such as MOV -> last argument.
            arity = {'MOV': 2, 'DMOV': 2, '$MOV': 2, 'BMOV': 3, 'FMOV': 3,
                     'DFMOV': 3, 'FROM': 4, 'DFROM': 4, 'TO': 4, 'DTO': 4,
                     'XCH': 2, 'DXCH': 2}.get(base_opcode(op))
            if arity is not None and len(args) != arity:
                fail(inst, f'{op} requires {arity} operands, got {len(args)}')
            if indices is None:
                uncertainty = uncertainty or f'unsupported instruction {op} at step {inst.step}'
            if indices and (min(indices) < 0 or max(indices) >= len(args)):
                fail(inst, f'{op}: missing destination operand')
            condition = current or '? (no preceding condition)'
            if uncertainty:
                condition = f'? ({uncertainty}); local condition: {condition}'
            # Instructions without a local destination (TO, control, calls)
            # remain visible. Unknown control flow taints subsequent results.
            for target in sorted(indices) if indices else [None]:
                out.append(CsvOutput(inst, condition, args[target] if target is not None else '', uncertainty))
            after_output = True
    if blocks or saved:
        raise ValueError('CSV ended with unbalanced logic stack')
    return out


def parse_csv_rows(path: Path, rows: list[dict[str, str]]) -> list[CsvOutput]:
    return parse_instructions(decode_instructions(rows, path.name))
