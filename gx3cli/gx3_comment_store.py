from __future__ import annotations

"""Read comment identities without merging words, bits, or remote units."""
import re
import sqlite3
from pathlib import Path
from gx3cli.gx3_device_name import format_device, parse_device_name

DEVICE_CODE_BY_TYPE = {
    'M': 1, 'SM': 2, 'L': 3, 'X': 16, 'Y': 17, 'B': 20,
    'D': 32, 'SD': 33, 'ZR': 40,
    'W': 48, 'SW': 49, 'T': 66, 'C': 70, 'ST': 74, 'P': 101,
}


def read_comment_records(path: Path):
    """Yield (full display name, base key or None, language texts).

    Base-only consumers intentionally omit bit/local/extended identities they
    cannot represent, rather than assigning their text to a different device.
    """
    con = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)
    try:
        columns = {r[1] for r in con.execute('pragma table_info(DEVICE_DATA)')}
        optional = lambda name: f'd.{name}' if name in columns else '0'
        fields = ','.join(optional(n) for n in ('ExtCode', 'ExtNo', 'IsLocal', 'DevNoHigh', 'BitNo'))
        query = ('SELECT d.SEQ,d.DevCode,d.DevNoLow,' + fields + ',c.CmtNo,c.CmtData '
                 'FROM DEVICE_DATA d LEFT JOIN COMMENT_DATA c ON c.DeviceSEQ=d.SEQ '
                 'AND coalesce(c.DelFlag,0)=0 ORDER BY d.SEQ,c.CmtNo')
        bycode = {v:k for k,v in DEVICE_CODE_BY_TYPE.items()}
        current = None
        name = ''; key = None; texts = {}
        for seq,code,num,ext,unit,local,high,bit,cno,text in con.execute(query):
            if seq != current:
                if current is not None and name:
                    yield name,key,texts
                current=seq; name=''; key=None; texts={}
                if local or high:
                    continue
                if ext == 208 and code == 35:
                    name=f'U{int(unit):X}\\G{num}'
                    key=(f'U{int(unit):X}G',int(num))
                elif not ext and code in bycode:
                    name=format_device(bycode[code],int(num));key=(bycode[code],int(num))
                if bit and name:
                    name += f'.{int(bit)-1:X}';key=None
            if cno is not None and text is not None:
                texts.setdefault(int(cno),str(text))
        if current is not None and name:
            yield name,key,texts
    finally:
        con.close()


def preferred_text(texts):
    return texts.get(5) or texts.get(6) or next((v for v in texts.values() if v), '')


def canonical_comment_name(text):
    # Dynamic addresses cannot safely borrow a base address's comment.
    match=re.fullmatch(r'(U[0-9A-F]+\\G\d+|[A-Z]+[0-9A-F]+)(?:\.([0-9A-F]+))?',text.upper())
    if not match:return None
    base,bit=match.groups()
    buffer=re.fullmatch(r'U([0-9A-F]+)\\G(\d+)',base)
    if buffer:base=f'U{int(buffer[1],16):X}\\G{int(buffer[2])}'
    else:
        try:base=format_device(*parse_device_name(base))
        except ValueError:return None
    return base+(f'.{int(bit,16):X}' if bit is not None else '')
