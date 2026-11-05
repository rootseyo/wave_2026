from pathlib import Path
import tempfile
import unittest
from export_data import read_export
from export_helpers import write_export, source_row


class ExportDataTests(unittest.TestCase):
    def test_column_name_lookup_including_issue_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row()])
            row = list(read_export(path))[0]
            self.assertEqual(row['field_id'], 'description')
            self.assertEqual(row['issue_status'], 'Resolved')
            self.assertEqual(row['content'], source_row()['content'])

    def test_encoded_html_and_entities_decode_only_export_layer(self):
        text = '<p>A &amp; B &lt;enabled&gt;<img src="https://jira.example/a?x=1&amp;y=2"></p>\nNext'
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row(content=text)])
            self.assertEqual(list(read_export(path))[0]['content'], text)

    def test_single_issue_finds_all_fields_without_parsing_other_rows(self):
        rows = [source_row(issue_key='OTHER-1'), source_row(), source_row(issue_key='OTHER-2'),
                source_row(field_id='customfield_123'), source_row(issue_key='OTHER-3')]
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', rows)
            from unittest.mock import patch
            import export_data
            with patch('export_data._parse', wraps=export_data._parse) as parse:
                selected = list(read_export(path, 'AAOSFRWDEV-543'))
                self.assertEqual(len(selected), 2)
                self.assertEqual(parse.call_count, 2)
                self.assertEqual([r['field_id'] for r in selected], ['description', 'customfield_123'])

    def test_nested_html_table_is_not_a_new_export_row(self):
        content = '<table><tr><td>x</td><td colspan="2">y</td></tr></table>'
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row(content=content), source_row(issue_key='OTHER-2')])
            self.assertEqual(len(list(read_export(path))), 2)

    def test_presentation_newlines_around_br_do_not_split_table_rows(self):
        from bs4 import BeautifulSoup
        from export_data import _content
        cell = BeautifulSoup('<td>|A|B|\n<br>\n|one|two|\n<br>\n\n<br>\nnext</td>', 'html.parser').td
        self.assertEqual(_content(cell), '|A|B|\n|one|two|\n\nnext')

    def test_not_found_returns_no_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_export(Path(directory) / 'data.html', [source_row()])
            self.assertEqual(list(read_export(path, 'MISSING-1')), [])


if __name__ == '__main__':
    unittest.main()
