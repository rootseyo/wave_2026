from unittest.mock import Mock, patch
import unittest
from migration_attachments import AttachmentMigrator, SourceClient, source_configs

MEDIA_ID = '4478e39c-cf9b-41d1-ba92-68589487cd75'


def redirect():
    return Mock(headers={'Location': 'https://api.media.atlassian.com/file/' + MEDIA_ID + '/binary?token=hidden'})


class AttachmentMigrationTests(unittest.TestCase):
    def test_preview_does_not_download_or_upload(self):
        cloud, source = Mock(), Mock()
        migrator = AttachmentMigrator(cloud, source, 'CFMT-3', [], dry_run=True)
        node = migrator('https://jira.example/provider?imgId=1&amp;fileName=a.png', image=True)
        self.assertEqual(node['type'], 'paragraph')
        source.get.assert_not_called()
        cloud.request.assert_not_called()
        self.assertEqual(migrator.actions[0]['filename'], 'a.png')

    def test_reuse_numeric_id_resolves_to_media_uuid(self):
        cloud, source = Mock(base_url='https://cloud.example'), Mock()
        cloud.request.return_value = redirect()
        migrator = AttachmentMigrator(cloud, source, 'CFMT-3', [{'id': '12345', 'filename': 'a.png'}], dry_run=False)
        node = migrator('a.png', image=True, width='551', height='252')
        attrs = node['content'][0]['attrs']
        self.assertEqual(attrs['id'], MEDIA_ID)
        self.assertEqual((attrs['width'], attrs['height']), (551, 252))
        source.get.assert_not_called()

    def test_filename_download_uses_original_issue_and_uploads_to_target(self):
        cloud, source = Mock(base_url='https://cloud.example'), Mock()
        source.find_filename.return_value = 'https://jira.example/secure/attachment/123/a.png'
        download = Mock(headers={'Content-Type': 'image/png'})
        download.iter_content.return_value = [b'PNG bytes']
        source.get.return_value = download
        uploaded = Mock()
        uploaded.json.return_value = [{'id': '6789', 'filename': 'a.png'}]
        cloud.request.side_effect = [uploaded, redirect()]
        migrator = AttachmentMigrator(cloud, source, 'CFMT-3', [], dry_run=False, source_issue_key='AAOSFRWDEV-281')
        migrator('a.png', image=True)
        source.find_filename.assert_called_once_with('AAOSFRWDEV-281', 'a.png')
        request = cloud.request.call_args_list[0]
        self.assertEqual(request.args[:2], ('POST', '/rest/api/3/issue/CFMT-3/attachments'))
        self.assertEqual(request.kwargs['headers']['X-Atlassian-Token'], 'no-check')
        self.assertEqual(migrator.actions[0]['action'], 'uploaded')

    def test_bad_media_redirect_does_not_invent_a_uuid(self):
        cloud, source = Mock(base_url='https://cloud.example'), Mock()
        cloud.request.return_value = Mock(headers={'Location': 'https://cloud.example/login'})
        migrator = AttachmentMigrator(cloud, source, 'CFMT-3', [{'id': '12345', 'filename': 'a.png'}], dry_run=False)
        with self.assertRaises(RuntimeError):
            migrator('a.png', image=True)

    def test_zip_attachment_becomes_cloud_link(self):
        cloud, source = Mock(base_url='https://cloud.example'), Mock()
        migrator = AttachmentMigrator(cloud, source, 'CFMT-3', [{'id': '12345', 'filename': 'vin_설정_실패_로그.zip'}], dry_run=False)
        node = migrator('vin_설정_실패_로그.zip', image=False)
        self.assertEqual(node['content'][0]['marks'][0]['attrs']['href'], 'https://cloud.example/rest/api/3/attachment/content/12345')
        cloud.request.assert_not_called()

    def test_unconfigured_host_rejected(self):
        source = SourceClient({'OPS': {'url': 'https://jira.example', 'candidates': []}})
        with self.assertRaises(ValueError):
            source.config_for('https://unconfigured.example/file')

    def test_pat_stays_bearer_and_cookie_is_separate_candidate(self):
        env = {'JIRA_DC_OPS_URL': 'https://jira.example', 'JIRA_DC_OPS_API_TOKEN': 'pat',
               'JIRA_DC_SESSION_COOKIE': 'JSESSIONID=cookie'}
        with patch.dict('os.environ', env, clear=True):
            config = source_configs()['OPS']
        self.assertEqual(config['candidates'][0]['headers']['Authorization'], 'Bearer pat')
        self.assertNotIn('Cookie', config['candidates'][0]['headers'])
        self.assertNotIn('Authorization', config['candidates'][1]['headers'])


if __name__ == '__main__':
    unittest.main()
