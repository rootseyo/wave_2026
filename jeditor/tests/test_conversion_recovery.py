import json
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from convert_adf import build_parser, main, process_export_row, run_file_conversion
from export_helpers import source_row, write_export
from migration_content import source_to_adf


class ConversionRecoveryTests(unittest.TestCase):
    def test_legacy_source_without_close_does_not_fail_successful_run(self):
        class LegacySource:
            pass
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [
                source_row(content='<b>Content</b>', migration_status='')])
            args = build_parser().parse_args(['--input', str(path), '--output-dir', directory])
            client = Mock(base_url='https://cloud.example', verify=True)
            client.read_fields.return_value = {'description': None, 'attachment': []}
            with patch('convert_adf.load_field_map', return_value={}), \
                 patch('migration_attachments.source_configs', return_value={}), \
                 patch('migration_attachments.SourceClient', return_value=LegacySource()), \
                 patch('convert_adf.progress_status'):
                self.assertEqual(run_file_conversion(args, client), 0)
            result_files = list(Path(directory).glob('*/results.jsonl'))
            result = json.loads(result_files[0].read_text().strip())
            self.assertEqual(result['status'], 'PREVIEW')

    def test_unexpected_error_records_message_traceback_and_module_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row()])
            with patch('convert_adf.load_environment'), \
                 patch('convert_adf.cloud_config', return_value=('https://cloud.example', ('user', 'token'))), \
                 patch('convert_adf.run_file_conversion', side_effect=AttributeError('missing close diagnostic')), \
                 patch('sys.stderr', new_callable=io.StringIO) as stderr:
                code = main(['--input', str(path), '--output-dir', directory])
            self.assertEqual(code, 1)
            logs = list(Path(directory).glob('error_*.json'))
            self.assertEqual(len(logs), 1)
            details = json.loads(logs[0].read_text())
            self.assertEqual(details['type'], 'AttributeError')
            self.assertIn('missing close diagnostic', details['message'])
            self.assertIn('Traceback', details['traceback'])
            self.assertIn('migration_attachments', details['modules'])
            self.assertIn('상세 오류 로그:', stderr.getvalue())

    def test_unmapped_custom_field_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(['--real-run'])
            client = Mock(base_url='https://cloud.example')
            client.read_fields.return_value = {'customfield_123': None, 'attachment': []}
            client.write_fields.side_effect = lambda key, fields: client.read_fields.return_value.update(fields)
            row = source_row(field_id='customfield_123', content='<b>Content</b>',
                             source_file='data.html', offset=1)
            with self.assertRaisesRegex(ValueError, 'Missing Cloud field mapping'):
                process_export_row(row, args, client, Mock(), Path(directory), {}, {})
            client.read_fields.assert_not_called()
            client.write_fields.assert_not_called()

            args.allow_same_field_ids = True
            result = process_export_row(row, args, client, Mock(), Path(directory), {}, {})
            self.assertEqual(result['status'], 'SUCCEEDED')
            self.assertIn('customfield_123', client.write_fields.call_args.args[1])

    def test_environment_is_converted_and_written(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(['--real-run', '--field', 'environment'])
            client = Mock(base_url='https://cloud.example')
            client.read_fields.return_value = {'environment': None, 'attachment': []}
            client.write_fields.side_effect = lambda key, fields: client.read_fields.return_value.update(fields)
            row = source_row(field_id='environment', content='<b>Environment details</b>',
                             source_file='data.html', offset=1)
            process_export_row(row, args, client, Mock(), Path(directory), {}, {})
            document = client.write_fields.call_args.args[1]['environment']
            self.assertEqual(document['content'][0]['content'][0]['text'], 'Environment details')

    def test_input_error_and_interrupt_preserve_running_result(self):
        for error in (ValueError('Broken next row'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                row = source_row(issue_key='A-1', migration_status='', offset=1)
                path = write_export(root / 'data.html', [row])
                args = build_parser().parse_args(['--input', str(path), '--output-dir', directory,
                                                  '--real-run', '--workers', '1', '--total', '2'])
                started = threading.Event()
                source = Mock()
                def process(*args):
                    started.set()
                    return {'issue': 'A-1', 'status': 'SUCCEEDED'}
                def export(*args):
                    yield row
                    if not started.wait(5):
                        raise AssertionError('Worker did not start')
                    raise error
                with patch('convert_adf.load_field_map', return_value={}), \
                     patch('migration_attachments.source_configs', return_value={}), \
                     patch('migration_attachments.SourceClient', return_value=source), \
                     patch('export_data.read_export', side_effect=export), \
                     patch('convert_adf.process_export_row', side_effect=process), \
                     patch('convert_adf.progress_status'), \
                     self.assertRaises(type(error)):
                    run_file_conversion(args, Mock(base_url='https://cloud.example', verify=True))
                source.close.assert_called_once()
                records = [json.loads(line) for report in root.glob('*/results.jsonl')
                           for line in report.read_text().splitlines()]
                self.assertEqual([r['status'] for r in records], ['SUCCEEDED'])
                journal = json.loads((root / 'file_checkpoints.json').read_text())
                statuses = [r['status'] for context in journal['contexts'].values() for r in context.values()]
                self.assertEqual(statuses, ['SUCCEEDED'])

    def test_wrapped_image_keeps_surrounding_formatted_text(self):
        media = {'type': 'mediaSingle', 'content': [{'type': 'media', 'attrs': {'id': 'image'}}]}
        attachment = Mock(return_value=media)
        doc = source_to_adf('<a href="https://example.com"><b>before<img src="photo.png">after</b></a>', attachment)
        attachment.assert_called_once_with('photo.png', image=True, width=None, height=None)
        self.assertEqual(doc['content'][1], media)
        for index, expected in ((0, 'before'), (2, 'after')):
            text = doc['content'][index]['content'][0]
            self.assertEqual(text['text'], expected)
            self.assertEqual({m['type'] for m in text['marks']}, {'strong', 'link'})


if __name__ == '__main__':
    unittest.main()
