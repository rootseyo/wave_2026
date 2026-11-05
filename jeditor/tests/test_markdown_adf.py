import copy
import unittest
from markdown_adf import convert_pipe_tables


def paragraph(text):
    return {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}


def document(*blocks):
    return {'type': 'doc', 'version': 1, 'content': list(blocks)}


class MarkdownTableTests(unittest.TestCase):
    def test_user_example_without_separator(self):
        result, count = convert_pipe_tables(document(paragraph('|ㅇㄹㅇㄹ| ㅇㄹㅇㄹㅇㄹ|')))
        self.assertEqual(count, 1)
        cells = result['content'][0]['content'][0]['content']
        self.assertEqual([c['content'][0]['content'][0]['text'] for c in cells], ['ㅇㄹㅇㄹ', 'ㅇㄹㅇㄹㅇㄹ'])
        self.assertTrue(all(c['type'] == 'tableCell' for c in cells))

    def test_markdown_header(self):
        result, count = convert_pipe_tables(document(paragraph('|A|B|\n|:---|---:|\n|가|나|')))
        self.assertEqual(count, 1)
        rows = result['content'][0]['content']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['content'][0]['type'], 'tableHeader')
        self.assertEqual(rows[1]['content'][0]['type'], 'tableCell')

    def test_wiki_header_and_separate_paragraphs(self):
        result, count = convert_pipe_tables(document(paragraph('||A||B||'), paragraph('|가|나|')))
        self.assertEqual(count, 1)
        self.assertEqual(len(result['content'][0]['content']), 2)
        self.assertEqual(result['content'][0]['content'][0]['content'][0]['type'], 'tableHeader')

    def test_hard_breaks_and_empty_cells(self):
        result, _ = convert_pipe_tables(document({'type': 'paragraph', 'content': [
            {'type': 'text', 'text': '|a||'}, {'type': 'hardBreak'}, {'type': 'text', 'text': '|b|c|'}]}))
        self.assertEqual(len(result['content'][0]['content']), 2)
        self.assertEqual(result['content'][0]['content'][0]['content'][1]['content'], [{'type': 'paragraph', 'content': []}])

    def test_preserve_marks_and_input(self):
        original = document({'type': 'paragraph', 'content': [
            {'type': 'text', 'text': '|'},
            {'type': 'text', 'text': 'bold', 'marks': [{'type': 'strong'}]},
            {'type': 'text', 'text': '|link', 'marks': [{'type': 'link', 'attrs': {'href': 'https://example.com'}}]},
            {'type': 'text', 'text': '|'}]})
        before = copy.deepcopy(original)
        result, _ = convert_pipe_tables(original)
        self.assertEqual(original, before)
        cells = result['content'][0]['content'][0]['content']
        self.assertEqual(cells[0]['content'][0]['content'][0]['marks'], [{'type': 'strong'}])
        self.assertEqual(cells[1]['content'][0]['content'][0]['marks'][0]['type'], 'link')

    def test_preserve_surrounding_blocks(self):
        before = paragraph('before\nkeep line breaks')
        after = paragraph('after')
        media = {'type': 'mediaSingle', 'content': [{'type': 'media', 'attrs': {'id': 'x', 'type': 'file', 'collection': ''}}]}
        result, count = convert_pipe_tables(document(before, paragraph('|a|b|'), after, media))
        self.assertEqual(count, 1)
        self.assertEqual(result['content'][0], before)
        self.assertEqual(result['content'][2:], [after, media])

    def test_existing_tables_and_code_untouched_and_idempotent(self):
        table = {'type': 'table', 'content': [{'type': 'tableRow', 'content': [
            {'type': 'tableCell', 'content': [paragraph('|literal|cell|')]}]}]}
        code = {'type': 'codeBlock', 'content': [{'type': 'text', 'text': '|a|b|'}]}
        original = document(table, code)
        self.assertEqual(convert_pipe_tables(original), (original, 0))
        converted, _ = convert_pipe_tables(document(paragraph('|a|b|')))
        self.assertEqual(convert_pipe_tables(converted), (converted, 0))

    def test_literal_pipes_and_non_tables(self):
        for text in ('normal a | b', '|one cell|', '|---|---|', '```|a|b|```'):
            original = document(paragraph(text))
            self.assertEqual(convert_pipe_tables(original), (original, 0))
        result, _ = convert_pipe_tables(document(paragraph(r'|a\|b|c|')))
        self.assertEqual(len(result['content'][0]['content'][0]['content']), 2)

    def test_fenced_code_text_is_untouched(self):
        original = document(paragraph('```text\n|a|b|\n```'))
        self.assertEqual(convert_pipe_tables(original), (original, 0))
        original = document(paragraph('~~~'), paragraph('|a|b|'), paragraph('~~~'))
        self.assertEqual(convert_pipe_tables(original), (original, 0))

    def test_multiple_tables(self):
        result, count = convert_pipe_tables(document(paragraph('|a|b|\n\n|c|d|')))
        self.assertEqual(count, 2)
        self.assertEqual([n['type'] for n in result['content']], ['table', 'paragraph', 'table'])

    def test_invalid_document(self):
        for value in (None, 'text', {}, {'type': 'doc', 'version': 1, 'content': 'bad'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                convert_pipe_tables(value)


if __name__ == '__main__':
    unittest.main()
