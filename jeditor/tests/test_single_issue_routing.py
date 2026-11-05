from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from convert_adf import build_parser, normalize_args, CloudClient, main
from export_helpers import write_export, source_row


class SingleIssueRoutingTests(unittest.TestCase):
    def test_modern_and_legacy_file_commands(self):
        cases = [(['--issue', 'AAOSFRWDEV-543'], False), (['AAOSFRWDEV-543'], True),
                 (['real', 'AAOSFRWDEV-543'], True), (['--dry-run', 'real', 'AAOSFRWDEV-543'], False),
                 (['dry-run', 'AAOSFRWDEV-543'], False)]
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row()])
            for argv, real in cases:
                with self.subTest(argv=argv):
                    parser = build_parser()
                    args = normalize_args(parser.parse_args(['--input', str(path), *argv]), parser)
                    self.assertEqual(args.issue, 'AAOSFRWDEV-543')
                    self.assertEqual(args.real_run, real)
                    self.assertIsNone(args.fields)

    def test_positional_issue_is_sandbox_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row()])
            parser = build_parser()
            args = normalize_args(parser.parse_args(['--input', str(path), 'aaosfrwdev-543']), parser)
            self.assertEqual(args.env, 'SANDBOX')
            self.assertEqual(args.workers, 1)
            self.assertTrue(args.real_run)
            with self.assertRaises(SystemExit):
                normalize_args(parser.parse_args(['--input', str(path), '--env', 'OPS', 'AAOSFRWDEV-543']), parser)

    def test_missing_issue_or_selected_field_stops_before_configuration_and_cloud(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row()])
            for extra in (['ABSENT-1'], ['AAOSFRWDEV-543', '--field', 'environment']):
                with self.subTest(extra=extra), patch('convert_adf.load_environment') as env, \
                     patch('convert_adf.cloud_config') as config, patch('convert_adf.CloudClient') as cloud, \
                     patch('sys.stderr') as stderr:
                    self.assertEqual(main(['--input', str(path), *extra]), 1)
                    message = ''.join(call.args[0] for call in stderr.write.call_args_list)
                    self.assertIn('없습니다', message)
                    self.assertIn('Cloud 요청은 수행하지 않았습니다', message)
                    env.assert_not_called()
                    config.assert_not_called()
                    cloud.assert_not_called()

    def test_positional_issue_executes_sandbox_real_run_without_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row(content='<b>Latest source</b>')])
            client = Mock(base_url='https://sandbox.example', verify=False)
            client.read_fields.return_value = {'description': None, 'attachment': []}
            client.write_fields.side_effect = lambda key, fields: client.read_fields.return_value.update(fields)
            with patch('convert_adf.load_environment'), \
                 patch('convert_adf.cloud_config', return_value=(client.base_url, ('user', 'token'))) as config, \
                 patch('convert_adf.CloudClient', return_value=client), \
                 patch('convert_adf.load_field_map', return_value={}), \
                 patch('migration_attachments.source_configs', return_value={}), \
                 patch('migration_attachments.SourceClient', return_value=Mock()), \
                 patch('builtins.input') as prompt:
                self.assertEqual(main(['--input', str(path), '--output-dir', directory, 'AAOSFRWDEV-543']), 0)
            config.assert_called_once_with('SANDBOX')
            prompt.assert_not_called()
            client.write_fields.assert_called_once()
            self.assertEqual(client.write_fields.call_args.args[0], 'AAOSFRWDEV-543')
            document = client.write_fields.call_args.args[1]['description']
            self.assertEqual(document['content'][0]['content'][0]['text'], 'Latest source')

    def test_read_only_selected_cloud_fields_without_metadata(self):
        session = Mock()
        session.request.return_value.status_code = 200
        session.request.return_value.json.return_value = {'fields': {'description': None},
            'names': {'description': '설명'}, 'schema': {'description': {'type': 'string', 'system': 'description'}}}
        client = CloudClient('https://example.atlassian.net', ('user', 'token'), session)
        client.read_fields('CFMT-3', ['description', 'attachment'])
        self.assertEqual(session.request.call_args.args[:2], ('GET', 'https://example.atlassian.net/rest/api/3/issue/CFMT-3'))
        self.assertEqual(session.request.call_args.kwargs['params']['fields'], 'description,attachment')
        self.assertEqual(session.request.call_args.kwargs['params']['expand'], 'names,schema')
        self.assertEqual(client.field_names['description'], '설명')
        self.assertEqual(client.field_schemas['description']['system'], 'description')

    def test_cloud_search_cannot_replace_export_input(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            normalize_args(parser.parse_args(['--jql', 'project = CFMT']), parser)


if __name__ == '__main__':
    unittest.main()
