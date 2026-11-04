"""Build a real lite index from synthetic comment records, then run lint."""
import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from gx3cli.extract_hmi_build_info import DEVICE_CODE_BY_TYPE
from gx3cli.gx3_index_lite import main as index_main
from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_lint import LintContext, check_unused_device
from gx3cli.gx3_synthetic_project import create_synthetic_project


class UnusedLimitTests(unittest.TestCase):
    def test_filtering_first_thousand_does_not_hide_the_remaining_3083(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            root = create_synthetic_project(work / 'source')
            with contextlib.closing(sqlite3.connect(root / '001_LDDB.db')) as con:
                con.execute('delete from LadderBlocks')
                rows = []
                for i in range(4083):
                    data, size, _ = generate_rung({'device': 'SM400'}, {'type': 'coil', 'device': f'M{i}'})
                    rows.append((f'_guid/synthetic-{i}', i * 1024, data, size))
                con.executemany('insert into LadderBlocks values (?,?,0,?,?,0,0)', rows)
                con.commit()
            with contextlib.closing(sqlite3.connect(root / '001_DC.db')) as con:
                con.execute('delete from DEVICE_DATA')
                con.execute('delete from COMMENT_DATA')
                con.executemany('insert into DEVICE_DATA values (?,?,0,0,?,0)',
                                [(i+1, DEVICE_CODE_BY_TYPE['M'], i) for i in range(4083)])
                con.executemany('insert into COMMENT_DATA values (?,5,?,0)',
                                [(i+1, 'spare' if i < 1000 else 'Synthetic status') for i in range(4083)])
                con.commit()
            db = work / 'index.sqlite'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(index_main(['build', '--root', str(root), '--out', str(db)]), 0)
            with contextlib.closing(sqlite3.connect(db)) as con:
                con.row_factory = sqlite3.Row
                found = check_unused_device(LintContext(root, [], {}, lite=con))
            self.assertEqual(len(found), 3083)
            self.assertEqual({item['device'] for item in found}, {f'M{i}' for i in range(1000, 4083)})


if __name__ == '__main__':
    unittest.main()
