import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from convert_adf import build_parser, normalize_args, run_file_conversion, process_export_row, cloud_config, verify_field_update
from export_helpers import write_export, source_row


class FileConversionTests(unittest.TestCase):
    def args(self, path, output, *extra):
        parser = build_parser()
        return normalize_args(parser.parse_args(['--input', str(path), '-o', str(output), *extra]), parser)

    def test_single_run_ignores_checkpoint_export_success_and_never_searches(self):
        client = Mock(base_url='https://cloud.example', verify=True)
        client.read_fields.return_value = {'description': None, 'attachment': []}
        client.write_fields.side_effect = lambda key, fields: client.read_fields.return_value.update(fields)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_export(root / 'data.html', [source_row()])
            (root / 'file_checkpoints.json').write_text('invalid JSON')
            args = self.args(path, root, '--issue', 'AAOSFRWDEV-543', '--real-run')
            doc = {'type': 'doc', 'version': 1, 'content': [{'type': 'paragraph', 'content': []}]}
            with patch('migration_content.source_to_adf', return_value=doc):
                self.assertEqual(run_file_conversion(args, client), 0)
            client.search_keys.assert_not_called()
            client.write_fields.assert_called_once_with('AAOSFRWDEV-543', {'description': doc})

    def test_backup_exists_before_put(self):
        client = Mock(base_url='https://cloud.example')
        client.read_fields.return_value = {'description': {'type': 'doc', 'version': 1, 'content': []}, 'attachment': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_export(root / 'data.html', [source_row()])
            args = self.args(path, root, '--issue', 'AAOSFRWDEV-543', '--real-run')
            row = {**source_row(), 'offset': 0, 'source_file': str(path)}
            def write(key, fields):
                snapshot = json.loads((root / 'AAOSFRWDEV-543_description_0.json').read_text())
                self.assertEqual(snapshot['status'], 'READY')
                self.assertEqual(snapshot['original_fields']['description'], client.read_fields.return_value['description'])
                client.read_fields.return_value.update(fields)
            client.write_fields.side_effect = write
            with patch('migration_content.source_to_adf', return_value={'type': 'doc', 'version': 1, 'content': []}):
                result = process_export_row(row, args, client, Mock(), root, {}, {})
            self.assertEqual(result['status'], 'SUCCEEDED')

    def test_explicit_field_mapping_and_target_override(self):
        client = Mock(base_url='https://cloud.example')
        client.read_fields.return_value = {'customfield_999': None, 'attachment': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_export(root / 'data.html', [source_row()])
            args = self.args(path, root, '--issue', 'AAOSFRWDEV-543', '--target-issue', 'CFMT-3')
            row = {**source_row(field_id='customfield_123'), 'offset': 0, 'source_file': str(path)}
            with patch('migration_content.source_to_adf', return_value={'type': 'doc', 'version': 1, 'content': []}):
                result = process_export_row(row, args, client, Mock(), root, {'customfield_123': 'customfield_999'}, {})
            self.assertEqual(result['issue'], 'CFMT-3')
            client.read_fields.assert_called_once_with('CFMT-3', ['customfield_999', 'attachment'])
            client.write_fields.assert_not_called()

    def test_config_does_not_mix_environment_credentials(self):
        env = {'JIRA_CLOUD_OPS_URL': 'https://ops.atlassian.net', 'JIRA_CLOUD_ADMIN_EMAIL': 'fallback', 'JIRA_CLOUD_API_TOKEN': 'fallback'}
        with patch.dict('os.environ', env, clear=True), self.assertRaises(ValueError):
            cloud_config('OPS')

    def test_accepted_write_logs_matching_readback(self):
        client = Mock(base_url='https://sandbox.example')
        client.read_fields.return_value = {'description': None, 'attachment': []}
        client.write_fields.side_effect = lambda key, fields: client.read_fields.return_value.update(fields)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_export(root / 'data.html', [source_row()])
            args = self.args(path, root, '--issue', 'AAOSFRWDEV-543', '--real-run')
            row = {**source_row(content='<b>converted</b>'), 'offset': 0, 'source_file': str(path)}
            result = process_export_row(row, args, client, Mock(), root, {}, {})
            self.assertEqual(result['status'], 'SUCCEEDED')
            self.assertTrue(result['verified'])
            self.assertTrue(result['changed'])
            snapshot = json.loads(Path(result['preview']).read_text())
            self.assertEqual(snapshot['verification']['fields']['description'], snapshot['fields']['description'])
            self.assertEqual(snapshot['cloud_url'], 'https://sandbox.example')
            client.read_fields.assert_called_with('AAOSFRWDEV-543', ['description'])

    def test_accepted_write_with_readback_difference_succeeds_with_warning(self):
        client = Mock(base_url='https://sandbox.example')
        client.read_fields.return_value = {'description': None, 'attachment': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_export(root / 'data.html', [source_row()])
            args = self.args(path, root, '--issue', 'AAOSFRWDEV-543', '--real-run')
            row = {**source_row(content='<b>converted</b>'), 'offset': 0, 'source_file': str(path)}
            with patch('convert_adf.time.sleep'):
                result = process_export_row(row, args, client, Mock(), root, {}, {})
            snapshot = json.loads((root / 'AAOSFRWDEV-543_description_0.json').read_text())
            self.assertEqual(snapshot['status'], 'SUCCEEDED')
            self.assertTrue(snapshot['write_accepted'])
            self.assertFalse(snapshot['verification']['matched'])
            self.assertIsNone(snapshot['verification']['fields']['description'])
            self.assertEqual(client.read_fields.call_count, 4)
            self.assertTrue(result['warnings'])
            self.assertFalse(result['verified'])
            client.write_fields.assert_called_once()

    def test_readback_error_does_not_reject_accepted_overwrite(self):
        client = Mock(base_url='https://sandbox.example')
        client.read_fields.side_effect = [{'description': None, 'attachment': []}, RuntimeError('HTTP 503')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_export(root / 'data.html', [source_row()])
            args = self.args(path, root, '--issue', 'AAOSFRWDEV-543', '--real-run')
            row = {**source_row(content='<b>converted</b>'), 'offset': 0, 'source_file': str(path)}
            result = process_export_row(row, args, client, Mock(), root, {}, {})
            self.assertEqual(result['status'], 'SUCCEEDED')
            self.assertTrue(result['write_accepted'])
            self.assertFalse(result['verified'])
            snapshot = json.loads(Path(result['preview']).read_text())
            self.assertEqual(snapshot['verification']['error']['message'], 'HTTP 503')

    def test_put_failure_is_still_failed(self):
        client = Mock(base_url='https://sandbox.example')
        client.read_fields.return_value = {'description': None, 'attachment': []}
        client.write_fields.side_effect = RuntimeError('Jira PUT failed: HTTP 400')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_export(root / 'data.html', [source_row()])
            args = self.args(path, root, '--issue', 'AAOSFRWDEV-543', '--real-run')
            row = {**source_row(content='<b>converted</b>'), 'offset': 0, 'source_file': str(path)}
            with self.assertRaisesRegex(RuntimeError, 'HTTP 400'):
                process_export_row(row, args, client, Mock(), root, {}, {})
            snapshot = json.loads((root / 'AAOSFRWDEV-543_description_0.json').read_text())
            self.assertEqual(snapshot['status'], 'FAILED')
            self.assertFalse(snapshot['write_accepted'])
            self.assertEqual(client.read_fields.call_count, 1)

    def test_existing_adf_equal_to_result_is_reported_as_unchanged(self):
        from migration_content import source_to_adf
        document = source_to_adf('<b>converted</b>', Mock())
        client = Mock(base_url='https://sandbox.example')
        client.read_fields.return_value = {'description': document, 'attachment': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_export(root / 'data.html', [source_row()])
            args = self.args(path, root, '--issue', 'AAOSFRWDEV-543', '--real-run')
            row = {**source_row(content='<b>converted</b>'), 'offset': 0, 'source_file': str(path)}
            result = process_export_row(row, args, client, Mock(), root, {}, {})
            self.assertTrue(result['verified'])
            self.assertFalse(result['changed'])
            self.assertIn('변경 없음', result['reason'])
            client.write_fields.assert_called_once()

    def test_wiki_string_readback_logs_type_difference_and_snippet_in_debug_mode(self):
        client = Mock()
        client.read_fields.return_value = {'customfield_123': 'h2. 1. Overview'}
        expected = {'type': 'doc', 'version': 1, 'content': []}
        with patch('convert_adf.time.sleep'), patch('sys.stdout', new_callable=io.StringIO) as output:
            verification = verify_field_update(client, 'A-1', 'customfield_123', expected,
                                               '기능 사양 (customfield_123)', debug=True)
        self.assertFalse(verification['matched'])
        first = verification['history'][0]['differences'][0]
        self.assertEqual(first['path'], '$')
        self.assertIn('dict', first['kind'])
        self.assertIn('str', first['kind'])
        self.assertIn('기능 사양', output.getvalue())
        self.assertIn('h2. 1. Overview', output.getvalue())

    def test_cloud_field_name_and_schema_are_logged_and_saved(self):
        client = Mock(base_url='https://sandbox.example')
        client.field_names = {'customfield_999': '실제 샌드박스 필드명'}
        client.field_schemas = {'customfield_999': {'type': 'string', 'custom': 'textarea'}}
        client.read_fields.return_value = {'customfield_999': None, 'attachment': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = build_parser().parse_args(['--dry-run'])
            row = {**source_row(field_id='customfield_123', field_name='원본 필드명', content='<b>example</b>'),
                   'offset': 0, 'source_file': 'data.html'}
            with patch('sys.stdout', new_callable=io.StringIO) as output:
                result = process_export_row(row, args, client, Mock(), root,
                                            {'customfield_123': 'customfield_999'}, {})
            self.assertIn('실제 샌드박스 필드명', output.getvalue())
            self.assertIn('변환 원문=data.html', output.getvalue())
            snapshot = json.loads(Path(result['preview']).read_text())
            self.assertEqual(snapshot['field_name'], '실제 샌드박스 필드명')
            self.assertEqual(snapshot['field_schema']['custom'], 'textarea')


if __name__ == '__main__':
    unittest.main()
