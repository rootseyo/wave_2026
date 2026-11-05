import unittest
from html_to_jira_wiki import html_to_jira_wiki


class HtmlToWikiTests(unittest.TestCase):
    def test_common_default_value_formatting(self):
        source = ('<h1>제목</h1><p><b>굵게</b> <a href="https://example.com">링크</a></p>'
                  '<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>'
                  '<ul><li>첫째</li><li>둘째</li></ul>'
                  '<p><span style="color:#e74c3c">변경</span></p>')
        result, warnings = html_to_jira_wiki(source)
        self.assertIn('h1. 제목', result)
        self.assertIn('*굵게* [링크|https://example.com]', result)
        self.assertIn('||A||B||\n|1|2|', result)
        self.assertIn('* 첫째\n* 둘째', result)
        self.assertIn('{color:#e74c3c}변경{color}', result)
        self.assertEqual(warnings, [])

    def test_merged_table_cells_are_reported(self):
        result, warnings = html_to_jira_wiki('<table><tr><td colspan="2">내용</td></tr></table>')
        self.assertIn('내용', result)
        self.assertTrue(warnings)

    def test_table_paragraphs_stay_on_one_wiki_row(self):
        source = '<table><tr><td><p>첫 문단</p><p>둘째 문단</p></td><td><a href="https://example.com">링크</a> | 구분</td></tr></table>'
        result, _ = html_to_jira_wiki(source)
        self.assertEqual(len(result.splitlines()), 1)
        self.assertIn('첫 문단' + chr(92) * 2 + '둘째 문단', result)
        self.assertIn('[링크|https://example.com]', result)
        self.assertIn('&#124; 구분', result)

    def test_colspan_and_rowspan_keep_columns_aligned(self):
        source = '<table><tr><td colspan="2">적용 기준</td><td>근거</td></tr><tr><td rowspan="2">A</td><td>B</td><td>C</td></tr><tr><td>D</td><td>E</td></tr></table>'
        result, warnings = html_to_jira_wiki(source)
        self.assertEqual(result.splitlines(), ['|적용 기준| |근거|', '|A|B|C|', '| |D|E|'])
        self.assertTrue(warnings)

    def test_gray_text_inside_table_is_preserved(self):
        result, _ = html_to_jira_wiki('<table><tr><td><span style="color:#999999">회색</span></td></tr></table>')
        self.assertIn('{color:#999999}회색{color}', result)


if __name__ == '__main__':
    unittest.main()
