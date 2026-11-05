import copy
import unittest
from markdown_adf import markdown_to_adf, convert_markdown_document


class MarkdownDocumentTests(unittest.TestCase):
    def test_heading_list_quote_code_and_rule(self):
        doc = markdown_to_adf('# Heading\n\n- one\n- two\n\n> quote\n\n```python\nprint(1)\n```\n\n---')
        self.assertEqual([n['type'] for n in doc['content']],
                         ['heading', 'bulletList', 'blockquote', 'codeBlock', 'rule'])
        self.assertEqual(doc['content'][3]['attrs']['language'], 'python')

    def test_inline_marks_and_safe_links(self):
        doc = markdown_to_adf('**bold** and *italic* ~~strike~~ `code` [link](https://example.com)')
        marks = {m['type'] for n in doc['content'][0]['content'] for m in n.get('marks', [])}
        self.assertEqual(marks, {'strong', 'em', 'strike', 'code', 'link'})

    def test_table_cell_markdown(self):
        doc = markdown_to_adf('|A|B|\n|---|---|\n|**bold**|[link](https://example.com)|')
        cells = doc['content'][0]['content'][1]['content']
        self.assertEqual(cells[0]['content'][0]['content'][0]['marks'], [{'type': 'strong'}])
        self.assertEqual(cells[1]['content'][0]['content'][0]['marks'][0]['type'], 'link')

    def test_markdown_bold_headers_have_no_literal_asterisks(self):
        doc = markdown_to_adf('|**Name**|**Value**|\n|one|two|')
        header = doc['content'][0]['content'][0]['content']
        self.assertEqual([cell['content'][0]['content'][0]['text'] for cell in header], ['Name', 'Value'])

    def test_wiki_headings(self):
        doc = markdown_to_adf('h2. Current spec\n\nh4. Provisioning')
        self.assertEqual([n['attrs']['level'] for n in doc['content']], [2, 4])

    def test_existing_rich_adf_preserved_and_idempotent(self):
        existing = {'type': 'doc', 'version': 1, 'content': [
            {'type': 'paragraph', 'content': [{'type': 'mention', 'attrs': {'id': 'user'}}]},
            {'type': 'codeBlock', 'content': [{'type': 'text', 'text': '**literal**'}]},
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': '**literal**', 'marks': [{'type': 'strong'}]}]},
            {'type': 'mediaSingle', 'content': [{'type': 'media', 'attrs': {'type': 'file', 'id': 'id', 'collection': ''}}]},
        ]}
        before = copy.deepcopy(existing)
        self.assertEqual(convert_markdown_document(existing), (existing, False, 0))
        self.assertEqual(existing, before)
        converted = markdown_to_adf('# heading\n\n|a|b|')
        self.assertFalse(convert_markdown_document(converted)[1])

    def test_plain_adf_is_not_rewritten(self):
        original = {'type': 'doc', 'version': 1, 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'hello\nworld'}]}]}
        self.assertFalse(convert_markdown_document(original)[1])

    def test_raw_html_is_literal_and_unsafe_link_is_not_a_mark(self):
        doc = markdown_to_adf('<script>bad()</script> [bad](javascript:alert(1))')
        self.assertNotIn('link', str(doc))
        self.assertIn('<script>', str(doc))

    def test_empty_string(self):
        self.assertEqual(markdown_to_adf(''), {'type': 'doc', 'version': 1, 'content': [{'type': 'paragraph', 'content': []}]})


if __name__ == '__main__':
    unittest.main()
