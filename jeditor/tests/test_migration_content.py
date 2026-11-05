from unittest.mock import Mock
import unittest
from migration_content import source_to_adf


def walk(node):
    yield node
    for child in node.get('content', []):
        yield from walk(child)


class MigrationContentTests(unittest.TestCase):
    def resolver(self):
        return Mock(return_value={'type': 'paragraph', 'content': [{'type': 'text', 'text': '[attachment]'}]})

    def test_wiki_single_column_empty_cells_and_headings(self):
        doc = source_to_adf('h2. Overview\n\n|KR|EU|\n|\xa0|\xa0|\n\n2.EV특화 여부\n|EV 특화|\n|\xa0|', self.resolver())
        tables = [n for n in walk(doc) if n['type'] == 'table']
        self.assertEqual(len(tables), 2)
        self.assertEqual(len(tables[1]['content'][0]['content']), 1)
        self.assertTrue(any(n['type'] == 'heading' for n in walk(doc)))
        self.assertEqual(tables[0]['content'][1]['content'][0]['content'][0]['content'], [])

    def test_adjacent_wiki_images_and_zip_attachment(self):
        resolver = self.resolver()
        source_to_adf('!https://jira.example/plugins/servlet/jeditor_file_provider?imgId=1&fileName=a.png|width=551,height=252!!image-2024.png|width=15,height=15!\n[^vin_설정_실패_로그.zip]', resolver)
        self.assertEqual(resolver.call_count, 3)
        self.assertEqual(resolver.call_args_list[0].kwargs, {'image': True, 'width': '551', 'height': '252'})
        self.assertEqual(resolver.call_args_list[1].args, ('image-2024.png',))
        self.assertFalse(resolver.call_args_list[2].kwargs['image'])

    def test_punctuation_is_not_mistaken_for_an_image(self):
        resolver = self.resolver()
        doc = source_to_adf('Hello! How are you! Fine.', resolver)
        resolver.assert_not_called()
        self.assertIn('Hello!', str(doc))

    def test_markdown_image_download_reference(self):
        resolver = self.resolver()
        source_to_adf('![screenshot](https://jira.example/secure/attachment/123/image.png)', resolver)
        resolver.assert_called_once_with('https://jira.example/secure/attachment/123/image.png', image=True, width=None, height=None)

    def test_code_with_inline_closing_marker_and_no_attachment_rewrite(self):
        resolver = self.resolver()
        doc = source_to_adf('{code:java}\nvalue = "!literal!"; {code}\nnext', resolver)
        code = next(n for n in walk(doc) if n['type'] == 'codeBlock')
        self.assertEqual(code['attrs']['language'], 'java')
        self.assertIn('!literal!', code['content'][0]['text'])
        resolver.assert_not_called()

    def test_html_table_inside_list_is_preserved_at_doc_level(self):
        warnings = []
        doc = source_to_adf('<ul><li>before<table><tbody><tr><td>ID</td><td>name</td></tr><tr><td>x</td><td colspan="10">Purpose<br>설명</td></tr></tbody></table></li></ul>', self.resolver(), warnings)
        table = next(n for n in doc['content'] if n['type'] == 'table')
        self.assertEqual(table['content'][1]['content'][1]['attrs']['colspan'], 10)
        self.assertTrue(warnings)

    def test_html_marks_images_and_dangerous_elements(self):
        resolver = self.resolver()
        doc = source_to_adf('<h4>[개요]</h4><p><b>굵게</b><u>밑줄</u></p><span><img src="https://jira.example/a.png" width="553" height="394"></span><script>danger()</script>', resolver)
        self.assertNotIn('danger()', str(doc))
        self.assertIn('underline', str(doc))
        resolver.assert_called_once()

    def test_line_breaks_are_hard_breaks_or_paragraphs(self):
        doc = source_to_adf('첫 줄\n다음 줄\n\n별도 문단', self.resolver())
        self.assertTrue(any(n['type'] == 'hardBreak' for n in walk(doc)))
        self.assertGreaterEqual(len(doc['content']), 2)

    def test_wiki_description_converts_headings_numbered_steps_image_bold_and_color(self):
        resolver = Mock(return_value={'type': 'mediaSingle', 'content': [
            {'type': 'media', 'attrs': {'id': '4478e39c-cf9b-41d1-ba92-68589487cd75', 'type': 'file', 'collection': ''}}]})
        source = '''h2. 1. 개요

테스트 본문

h2. 2. 현재 사양

!pastedImage.png|width=752,height=509,alt="pastedImage.png"!

h2. 3. 변경 사양

# 첫 번째 단계

# 두 번째 단계

# 세 번째 단계

*6.6. 기능*

{color:#e74c3c}(내용추가) 변경 내용{color}

{color:#e74c3c}(내용추가) 다른 변경 내용{color}'''
        doc = source_to_adf(source, resolver)
        nodes = list(walk(doc))
        self.assertEqual(sum(n['type'] == 'heading' for n in nodes), 3)
        self.assertEqual(sum(n['type'] == 'listItem' for n in nodes), 3)
        self.assertEqual(sum(n['type'] == 'media' for n in nodes), 1)
        self.assertEqual(sum(n['type'] == 'table' for n in nodes), 0)
        marks = [m for n in nodes for m in n.get('marks', [])]
        self.assertTrue(any(m['type'] == 'strong' for m in marks))
        self.assertEqual(sum(m['type'] == 'textColor' and m['attrs']['color'] == '#e74c3c' for m in marks), 2)
        text = ''.join(n.get('text', '') for n in nodes)
        self.assertNotIn('{color', text)
        self.assertNotIn('h2.', text)
        resolver.assert_called_once_with('pastedImage.png', image=True, width='752', height='509')

    def test_wiki_colors_inside_code_are_literal(self):
        doc = source_to_adf('{code}\n{color:red}literal{color}\n{code}', self.resolver())
        code = next(n for n in walk(doc) if n['type'] == 'codeBlock')
        self.assertIn('{color:red}', code['content'][0]['text'])

    def test_nested_layout_unwraps_outer_tables_and_preserves_leaf_tables(self):
        source = ('<table><tr><td><b>바깥 제목</b></td><td><p>설명</p>'
                  '<table><tr><td>중간 제목<table><tr><th colspan="2" style="background-color:#eeeeee">가장 안쪽</th></tr>'
                  '<tr><td>A</td><td>B</td></tr></table>중간 뒤</td></tr></table>'
                  '<p>표 뒤</p><table><tr><td>C</td></tr></table></td></tr></table>')
        warnings = []
        doc = source_to_adf(source, self.resolver(), warnings)
        tables = [n for n in walk(doc) if n['type'] == 'table']
        self.assertEqual(len(tables), 2)
        self.assertEqual(sum(n['type'] == 'table' for n in doc['content']), 2)
        self.assertFalse(any(n['type'] == 'table' for t in tables for c in t['content'] for n in walk(c)))
        header = tables[0]['content'][0]['content'][0]
        self.assertEqual(header['attrs'], {'colspan': 2, 'background': '#eeeeee'})
        text = [n['text'] for n in walk(doc) if n['type'] == 'text']
        self.assertEqual(text, ['바깥 제목', '설명', '중간 제목', '가장 안쪽', 'A', 'B', '중간 뒤', '표 뒤', 'C'])
        self.assertTrue(any(m['type'] == 'strong' for n in walk(doc) for m in n.get('marks', [])))
        self.assertEqual(len(warnings), 2)

    def test_outer_numbered_labels_are_plain_paragraphs_above_inner_tables(self):
        source = ('<table><tr><td><p>1. 사양 현황</p><p>(변경전)</p></td>'
                  '<td><table><tr><td>이전 데이터</td></tr></table></td></tr>'
                  '<tr><td><p>2. 안건 상세</p><p>(변경후)</p></td>'
                  '<td><table><tr><td>변경 데이터</td></tr></table></td></tr></table>')
        doc = source_to_adf(source, self.resolver())
        self.assertEqual([n['type'] for n in doc['content']], ['paragraph', 'table', 'paragraph', 'table'])
        self.assertEqual(''.join(n['text'] for n in doc['content'][0]['content']), '1. 사양 현황 (변경전)')
        self.assertEqual(''.join(n['text'] for n in doc['content'][2]['content']), '2. 안건 상세 (변경후)')

    def test_empty_colgroup_table_does_not_abort_field_conversion(self):
        warnings = []
        source = '<p>앞</p><table><colgroup><col width="100"></colgroup></table><p>뒤</p>'
        doc = source_to_adf(source, self.resolver(), warnings)
        self.assertEqual([n['text'] for n in walk(doc) if n['type'] == 'text'], ['앞', '뒤'])
        self.assertFalse(any(n['type'] == 'table' for n in walk(doc)))
        self.assertTrue(warnings)


if __name__ == '__main__':
    unittest.main()
