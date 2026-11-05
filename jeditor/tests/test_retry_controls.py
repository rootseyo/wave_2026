from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from convert_adf import Checkpoints, build_parser, normalize_args, run_file_conversion
from export_helpers import write_export, source_row


class RetryControlTests(unittest.TestCase):
    def test_checkpoint_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Checkpoints(Path(directory) / 'checkpoint.json', 'test')
            journal.record('A-1', 'SUCCEEDED')
            journal.record('A-2', 'SKIPPED')
            self.assertFalse(journal.should_resume('A-1', single_issue=True))
            self.assertTrue(journal.should_resume('A-1', retry_skipped=True))
            self.assertFalse(journal.should_resume('A-2', retry_skipped=True))
            self.assertTrue(journal.should_resume('A-2'))

    def test_retry_skipped_rows_but_preserve_export_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_export(root / 'data.html', [source_row(migration_status='Skipped (Screen)', issue_key='A-1'), source_row(issue_key='A-2')])
            parser = build_parser()
            args = normalize_args(parser.parse_args(['--input', str(path), '-o', str(root), '--retry-skipped']), parser)
            client = Mock(base_url='https://cloud.example', verify=True)
            result = {'issue': 'A-1', 'field': 'description', 'status': 'PREVIEW', 'tables': 1}
            with patch('convert_adf.process_export_row', return_value=result) as process:
                self.assertEqual(run_file_conversion(args, client), 0)
                process.assert_called_once()
                self.assertEqual(process.call_args.args[0]['issue_key'], 'A-1')


if __name__ == '__main__':
    unittest.main()
