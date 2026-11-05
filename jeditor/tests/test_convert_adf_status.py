import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from convert_adf import build_parser, format_duration, normalize_args
from export_data import count_export_rows
from export_helpers import source_row, write_export


class ConvertAdfStatusTests(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(None), '계산 중...')
        self.assertEqual(format_duration(-1), '계산 중...')
        self.assertEqual(format_duration(0), '0초')
        self.assertEqual(format_duration(45), '45초')
        self.assertEqual(format_duration(60), '1분')
        self.assertEqual(format_duration(65), '1분 05초')
        self.assertEqual(format_duration(3600), '1시간')
        self.assertEqual(format_duration(7200), '2시간')
        self.assertEqual(format_duration(6250), '1시간 44분')
        self.assertEqual(format_duration(86400), '1일')
        self.assertEqual(format_duration(90000), '1일 1시간')

    def test_count_export_rows_nonexistent_and_empty(self):
        self.assertEqual(count_export_rows('/non/existent/file.html'), 0)
        with tempfile.NamedTemporaryFile(suffix='.html') as empty_file:
            self.assertEqual(count_export_rows(empty_file.name), 0)

    def test_count_export_rows_with_nested_table_and_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.html'
            nested_html = '<table><tr><td>inner</td></tr></table>'
            rows = [
                source_row(project_key='PROJA', field_id='description', content=nested_html),
                source_row(project_key='PROJA', field_id='customfield_1001', content='text 1'),
                source_row(project_key='PROJB', field_id='description', content='text 2'),
                source_row(project_key='PROJB', field_id='customfield_1002', content='text 3'),
            ]
            write_export(path, rows)

            # All rows
            self.assertEqual(count_export_rows(path), 4)

            # Project filter
            self.assertEqual(count_export_rows(path, projects=['PROJA']), 2)
            self.assertEqual(count_export_rows(path, projects=['PROJB']), 2)
            self.assertEqual(count_export_rows(path, projects=['PROJC']), 0)

            # Field filter
            self.assertEqual(count_export_rows(path, fields=['description']), 2)
            self.assertEqual(count_export_rows(path, fields=['customfield_1001']), 1)

            # Both filters
            self.assertEqual(count_export_rows(path, projects=['PROJA'], fields=['description']), 1)
            self.assertEqual(count_export_rows(path, projects=['PROJB'], fields=['customfield_1001']), 0)

    def test_cli_total_argument(self):
        parser = build_parser()
        args = parser.parse_args(['--total', '40000'])
        self.assertEqual(args.total, 40000)

        args = parser.parse_args(['-t', '250'])
        self.assertEqual(args.total, 250)

        args = parser.parse_args([])
        self.assertIsNone(args.total)

    def test_cli_total_validation(self):
        parser = build_parser()
        with tempfile.NamedTemporaryFile(suffix='.html') as dummy_input:
            args = parser.parse_args(['--input', dummy_input.name, '--total', '0'])
            with self.assertRaises(SystemExit):
                normalize_args(args, parser)

            args = parser.parse_args(['--input', dummy_input.name, '--total', '-10'])
            with self.assertRaises(SystemExit):
                normalize_args(args, parser)

    def test_status_bar_rendering_with_total(self):
        from convert_adf import run_file_conversion
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.html'
            rows = [source_row(issue_key=f'TEST-{i}') for i in range(5)]
            write_export(path, rows)

            parser = build_parser()
            args = parser.parse_args(['--input', str(path), '--output-dir', directory, '--total', '5'])
            normalize_args(args, parser)

            captured_status = []
            mock_client = Mock()
            mock_client.base_url = 'https://mock.jira.com'
            mock_client.verify = True

            with patch('convert_adf.progress_status', side_effect=captured_status.append):
                with patch('convert_adf.process_export_row', return_value={'issue': 'TEST-1', 'status': 'PREVIEW'}):
                    run_file_conversion(args, mock_client)

            # Check that captured status messages contain progress and ETA
            progress_messages = [msg for msg in captured_status if '진행률' in msg]
            self.assertTrue(len(progress_messages) > 0)
            last_progress = progress_messages[-1]
            self.assertIn('진행률 100.0% (5/5)', last_progress)
            self.assertIn('예상 소요 시간: 완료', last_progress)
            self.assertIn('건/s', last_progress)

    def test_status_bar_auto_detects_total_from_file(self):
        from convert_adf import run_file_conversion
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.html'
            rows = [source_row(issue_key=f'AUTO-{i}') for i in range(3)]
            write_export(path, rows)

            parser = build_parser()
            # Do NOT pass --total
            args = parser.parse_args(['--input', str(path), '--output-dir', directory])
            normalize_args(args, parser)

            captured_status = []
            mock_client = Mock()
            mock_client.base_url = 'https://mock.jira.com'
            mock_client.verify = True

            with patch('convert_adf.progress_status', side_effect=captured_status.append):
                with patch('convert_adf.process_export_row', return_value={'issue': 'AUTO-1', 'status': 'PREVIEW'}):
                    run_file_conversion(args, mock_client)

            progress_messages = [msg for msg in captured_status if '진행률' in msg]
            self.assertTrue(len(progress_messages) > 0)
            last_progress = progress_messages[-1]
            self.assertIn('진행률 100.0% (3/3)', last_progress)
            self.assertIn('예상 소요 시간: 완료', last_progress)

    def test_checkpoints_batch_flushing_and_memory(self):
        import json
        from convert_adf import Checkpoints
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'checkpoints.json'
            journal = Checkpoints(path, 'ctx', flush_interval=10)
            for i in range(9):
                journal.record(f'ID-{i}', 'SUCCEEDED')
            # 9 items recorded, should not have flushed to disk yet (flush_interval=10)
            self.assertFalse(path.exists())
            self.assertEqual(journal._dirty, 9)

            # 10th item triggers flush
            journal.record('ID-9', 'SUCCEEDED')
            self.assertTrue(path.exists())
            self.assertEqual(journal._dirty, 0)
            data = json.loads(path.read_text())
            self.assertEqual(len(data['contexts']['ctx']), 10)

            # Explicit flush
            journal.record('ID-10', 'SUCCEEDED')
            self.assertEqual(journal._dirty, 1)
            journal.flush()
            self.assertEqual(journal._dirty, 0)
            data = json.loads(path.read_text())
            self.assertEqual(len(data['contexts']['ctx']), 11)

    def test_bounded_managers_eviction(self):
        from convert_adf import process_export_row
        with tempfile.TemporaryDirectory() as directory:
            mock_client = Mock(base_url='https://cloud.example')
            mock_client.read_fields.return_value = {'description': None, 'attachment': []}
            mock_source = Mock()
            managers = {}
            row = source_row()
            input_path = write_export(Path(directory) / 'data.html', [row])
            row = {**row, 'source_file': str(input_path), 'offset': 0}

            parser = build_parser()
            args = parser.parse_args(['--dry-run', '--issue', 'DUMMY-1', '--input', str(input_path)])
            normalize_args(args, parser)

            # Pre-fill managers with 300 entries
            for i in range(300):
                managers[f'OLD-{i}'] = Mock()

            # Calling process_export_row with registry_lock should bound managers size to 256
            import threading
            lock = threading.Lock()
            with patch('migration_content.source_to_adf', return_value={'type': 'doc', 'content': []}):
                process_export_row(row, args, mock_client, mock_source, Path(directory), {}, managers, registry_lock=lock, active=set())

            self.assertLessEqual(len(managers), 256)


if __name__ == '__main__':
    unittest.main()
