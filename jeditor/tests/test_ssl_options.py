from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import Mock, patch
from convert_adf import CloudClient, build_parser, ssl_verification


class SslOptionsTests(unittest.TestCase):
    def args(self, *options):
        return build_parser().parse_args(['--issue', 'CFMT-3', '--env', 'OPS', *options])

    def test_default_disables_verification(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertIs(ssl_verification(self.args()), False)

    def test_cli_insecure_and_legacy_env(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertIs(ssl_verification(self.args('--insecure')), False)
        with patch.dict('os.environ', {'VERIFY_SSL': 'false'}, clear=True):
            self.assertIs(ssl_verification(self.args()), False)

    def test_ca_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ca.pem'
            path.write_text('test certificate placeholder')
            with patch.dict('os.environ', {'VERIFY_SSL': 'true'}, clear=True), self.assertRaises(ValueError):
                ssl_verification(self.args('--ca-bundle', str(path)))

    def test_environment_cannot_enable_verification(self):
        with patch.dict('os.environ', {'VERIFY_SSL': 'true', 'REQUESTS_CA_BUNDLE': '/missing'}, clear=True):
            self.assertIs(ssl_verification(self.args()), False)

    def test_missing_ca_is_an_error(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(ValueError):
            ssl_verification(self.args('--ca-bundle', '/missing/ca.pem'))

    def test_all_requests_receive_verify_setting(self):
        session = Mock()
        session.request.return_value.status_code = 200
        client = CloudClient('https://example.atlassian.net', ('email', 'token'), session, verify=False)
        client.request('GET', '/rest/api/3/issue/CFMT-3')
        self.assertIs(session.request.call_args.kwargs['verify'], False)

    def test_ssl_error_has_actionable_message_without_raw_url(self):
        session = Mock()
        session.request.side_effect = ssl.SSLError('CERTIFICATE_VERIFY_FAILED secret-value')
        client = CloudClient('https://example.atlassian.net', ('email', 'token'), session)
        with self.assertRaises(RuntimeError) as caught:
            client.request('GET', '/rest/api/3/issue/CFMT-3')
        self.assertIn('SSL 설정', str(caught.exception))
        self.assertNotIn('secret-value', str(caught.exception))
        session.request.assert_called_once()


if __name__ == '__main__':
    unittest.main()
