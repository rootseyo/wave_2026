from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from convert_adf import build_parser, interactive_args, normalize_args, ssl_verification, main
from export_helpers import write_export, source_row
from convert_adf import confirm_interactive_run


class InteractiveArgsTests(unittest.TestCase):
    def test_confirmation_requires_successful_auth_and_explicit_yes(self):
        args = build_parser().parse_args([])
        client = Mock(base_url='https://example.atlassian.net', auth=('user', 'secret'), verify=False)
        client.request.return_value.status_code = 200
        client.request.return_value.json.return_value = {'accountId': 'account'}
        for answer, expected in [('y', True), ('n', False), ('', False)]:
            with patch('builtins.input', return_value=answer):
                self.assertIs(confirm_interactive_run(args, client), expected)
        client.request.assert_called_with('GET', '/rest/api/3/myself', allow_redirects=False)
        client.request.side_effect = RuntimeError('HTTP 401')
        with patch('builtins.input') as prompt, self.assertRaises(RuntimeError):
            confirm_interactive_run(args, client)
        prompt.assert_not_called()

    def answers(self, path, values):
        parser = build_parser()
        with patch('builtins.input', side_effect=[str(path), *values]):
            return normalize_args(interactive_args(parser), parser)

    def test_single_source_and_target_override_ssl_skip_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row()])
            args = self.answers(path, ['2', '1', 'AAOSFRWDEV-543', 'CFMT-3', '', '', '1', '', '', '1'])
            self.assertEqual(args.issue, 'AAOSFRWDEV-543')
            self.assertEqual(args.target_issue, 'CFMT-3')
            self.assertEqual(args.env, 'OPS')
            self.assertFalse(args.real_run)
            self.assertEqual(args.workers, 1)
            self.assertIs(ssl_verification(args), False)

    def test_project_retry_workers_fields_and_real_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row()])
            args = self.answers(path, ['2', '2', 'CFMT,TEST', 'description,customfield_123', '1', '', '1', '8', 'reports', '2'])
            self.assertEqual(args.project, 'CFMT,TEST')
            self.assertTrue(args.retry_skipped)
            self.assertEqual(args.workers, 8)
            self.assertTrue(args.real_run)
            with patch.dict('os.environ', {'VERIFY_SSL': 'false'}, clear=True):
                self.assertIs(ssl_verification(args), False)

    def test_file_all_and_preserve_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row()])
            args = self.answers(path, ['1', '3', '', '2', '', '2', '', '', '1'])
            self.assertIsNone(args.issue)
            self.assertIsNone(args.project)
            self.assertFalse(args.retry_skipped)
            self.assertEqual(args.source_env, 'STG')
            self.assertIs(ssl_verification(args), False)

    def test_eof_cancels_without_configuration_or_network(self):
        with patch('sys.argv', ['convert_adf.py']), patch('sys.stdin.isatty', return_value=True), \
             patch('builtins.input', side_effect=EOFError), patch('convert_adf.load_environment') as load, \
             patch('convert_adf.run_file_conversion') as run:
            self.assertEqual(main(), 130)
            load.assert_not_called()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
