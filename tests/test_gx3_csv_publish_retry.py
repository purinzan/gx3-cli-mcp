"""Simulated WinError 5 only; no Windows lock or antivirus claim is made."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gx3cli import gx3_csv_export as exporter
from gx3cli.gx3_synthetic_project import create_synthetic_project


def denied(code=5):
    error = PermissionError('synthetic rename denial')
    error.winerror = code
    return error


class CsvRetryTests(unittest.TestCase):
    def test_ninth_and_tenth_attempt_can_still_succeed(self):
        for failures in (8, 9):
            with self.subTest(failures=failures), tempfile.TemporaryDirectory() as tmp:
                stage, target = Path(tmp)/'stage', Path(tmp)/'target'
                stage.mkdir()
                (stage/'payload').write_text('complete')
                real = exporter.os.rename
                attempts = []
                def rename(source, destination):
                    attempts.append(None)
                    if len(attempts) <= failures:
                        raise denied()
                    real(source, destination)
                with patch.object(exporter.sys, 'platform', 'win32'), patch.object(exporter.os, 'rename', side_effect=rename), \
                        patch.object(exporter.time, 'sleep') as sleep:
                    exporter.rename_with_retry(stage, target)
                self.assertEqual((len(attempts), sleep.call_count), (failures + 1, failures))
                delays = [call.args[0] for call in sleep.call_args_list]
                self.assertTrue(all(left < right for left, right in zip(delays, delays[1:])))
                self.assertEqual((target/'payload').read_text(), 'complete')

    def test_transient_windows_access_denial_retries_and_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            root = create_synthetic_project(work / 'source')
            rename = exporter.os.rename
            calls = []
            def transient(source, destination):
                calls.append((source, destination))
                if len(calls) <= 3:
                    raise denied()
                return rename(source, destination)
            with patch.object(exporter.sys, 'platform', 'win32'), patch.object(exporter.os, 'rename', side_effect=transient), \
                    patch.object(exporter.time, 'sleep') as sleep:
                exporter.export_csv(root, work / 'output', kind='comments', format='analysis')
            self.assertEqual(len(calls), 4)
            for call, expected in zip(sleep.call_args_list, [0.05, 0.1, 0.15]):
                self.assertAlmostEqual(call.args[0], expected)
            self.assertTrue((work / 'output' / 'COMMENT.csv').is_file())
            self.assertEqual(list(work.glob('.gx3-csv-*')), [])

    def test_other_errors_and_non_windows_do_not_retry(self):
        for platform, error in [('darwin', denied()), ('win32', denied(32)), ('win32', PermissionError('no winerror'))]:
            with self.subTest(platform=platform, error=error), tempfile.TemporaryDirectory() as tmp:
                stage, target = Path(tmp)/'stage', Path(tmp)/'target'
                stage.mkdir()
                with patch.object(exporter.sys, 'platform', platform), patch.object(exporter.os, 'rename', side_effect=error) as rename, \
                        patch.object(exporter.time, 'sleep') as sleep:
                    with self.assertRaises(PermissionError) as caught:
                        exporter.rename_with_retry(stage, target)
                self.assertIs(caught.exception, error)
                self.assertEqual(rename.call_count, 1)
                sleep.assert_not_called()

    def test_tenth_attempt_raises_and_export_removes_the_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            root = create_synthetic_project(work / 'source')
            error = denied()
            with patch.object(exporter.sys, 'platform', 'win32'), patch.object(exporter.os, 'rename', side_effect=error) as rename, \
                    patch.object(exporter.time, 'sleep') as sleep:
                with self.assertRaises(PermissionError) as caught:
                    exporter.export_csv(root, work / 'output', kind='comments', format='analysis')
            self.assertIs(caught.exception, error)
            self.assertEqual((rename.call_count, sleep.call_count), (10, 9))
            self.assertFalse((work/'output').exists())
            self.assertEqual(list(work.glob('.gx3-csv-*')), [])

    def test_a_target_created_during_the_wait_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, target = Path(tmp)/'stage', Path(tmp)/'target'
            stage.mkdir()
            def create_target(_delay):
                target.mkdir()
                (target/'sentinel').write_text('preserve')
            error = denied()
            with patch.object(exporter.sys, 'platform', 'win32'), patch.object(exporter.os, 'rename', side_effect=error) as rename, \
                    patch.object(exporter.time, 'sleep', side_effect=create_target):
                with self.assertRaises(PermissionError):
                    exporter.rename_with_retry(stage, target)
            self.assertEqual(rename.call_count, 1)
            self.assertEqual((target/'sentinel').read_text(), 'preserve')

    def test_an_existing_destination_preserves_the_original_rename_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, target = Path(tmp)/'stage', Path(tmp)/'target'
            stage.mkdir()
            target.mkdir()
            error = denied()
            with patch.object(exporter.sys, 'platform', 'win32'), patch.object(exporter.os, 'rename', side_effect=error) as rename, \
                    patch.object(exporter.time, 'sleep') as sleep:
                with self.assertRaises(PermissionError) as caught:
                    exporter.rename_with_retry(stage, target)
            self.assertIs(caught.exception, error)
            self.assertEqual(rename.call_count, 1)
            sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
