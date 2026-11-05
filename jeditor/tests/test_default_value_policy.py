"""Run in the separate test environment: python -m unittest discover -s tests -p test_default_value_policy.py"""
import unittest
from default_value_policy import Policy, default_state


class PolicyTests(unittest.TestCase):
    def row(self, xml='<string></string>', issue_type=None):
        return dict(project_key='ATCU', field_id='customfield_1', context_id=1,
                    context_scope='Project', field_configuration_id=10,
                    default_value_id=20, default_value_xml=xml, issue_type_id=issue_type)

    def snapshot(self, rows):
        return dict(captured_at='test', environment='ops', checked_projects=['ATCU'],
                    defaults=rows, default_layout_ids=['100'], issue_type_ids=['1', '2'],
                    renderer_schemes=[dict(project_key='ATCU', scheme_id=None)],
                    renderer_items=[dict(layout_id='100', field_id='customfield_1', renderer='jeditor-renderer')])

    def record(self, types=None):
        return dict(project='ATCU', settings_validation={'normalized': {
            'autofill_fields': ['customfield_1'], 'autofill_types': types or ['1']}})

    def test_empty_html_and_text(self):
        self.assertEqual(default_state(self.row('<string>&lt;p&gt;&lt;br&gt;&lt;/p&gt;</string>')), 'EMPTY')
        self.assertEqual(default_state(self.row('<string>일반 텍스트</string>')), 'PRESENT')
        self.assertEqual(default_state(self.row('<string>0</string>')), 'PRESENT')
        self.assertEqual(default_state(self.row('<unknown/>')), 'UNKNOWN')
        self.assertEqual(default_state(self.row('<broken')), 'UNKNOWN')

    def test_default_priority(self):
        result = Policy(self.snapshot([self.row('<string>기본값</string>')])).evaluate(self.record())
        self.assertEqual(result['status'], 'DEFAULT_VALUE_PRIORITY')

    def test_partial_overlap_does_not_pass(self):
        rows = [self.row('<string>값</string>', '1'), self.row('<string/>', '2')]
        result = Policy(self.snapshot(rows)).evaluate(self.record(['ALL']))
        self.assertEqual(result['status'], 'PARTIAL_DEFAULT_VALUE')

    def test_renderer_and_coverage(self):
        snapshot = self.snapshot([self.row()])
        self.assertEqual(Policy(snapshot).evaluate(self.record())['status'], 'PASS')
        snapshot['renderer_items'][0]['renderer'] = 'wiki-renderer'
        self.assertEqual(Policy(snapshot).evaluate(self.record())['status'], 'NOT_JEDITOR_RENDERER')
        snapshot['checked_projects'] = []
        self.assertEqual(Policy(snapshot).evaluate(self.record())['status'], 'UNKNOWN')

    def test_missing_context_is_unknown(self):
        self.assertEqual(Policy(self.snapshot([])).evaluate(self.record())['status'], 'UNKNOWN')

    def test_issue_specific_renderer_overrides_default(self):
        snapshot = self.snapshot([self.row()])
        snapshot['renderer_schemes'] = [
            dict(project_key='ATCU', scheme_id=1, issue_type_id=None, layout_id=100),
            dict(project_key='ATCU', scheme_id=1, issue_type_id='1', layout_id=200)]
        snapshot['renderer_items'].append(dict(layout_id=200, field_id='customfield_1', renderer='wiki-renderer'))
        self.assertEqual(Policy(snapshot).evaluate(self.record())['status'], 'NOT_JEDITOR_RENDERER')
        self.assertEqual(Policy(snapshot).evaluate(self.record(['2']))['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
