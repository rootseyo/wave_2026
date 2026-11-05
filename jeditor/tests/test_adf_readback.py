import copy
import io
import unittest
from unittest.mock import Mock, patch

from convert_adf import normalize_adf_for_comparison, verify_field_update


class AdfReadbackTests(unittest.TestCase):
    def documents(self):
        expected = {'type': 'doc', 'version': 1, 'content': [
            {'type': 'table', 'content': [{'type': 'tableRow', 'content': [
                {'type': 'tableCell', 'content': [{'type': 'paragraph', 'content': []}]},
                {'type': 'tableCell', 'attrs': {'colspan': 2, 'background': '#eeeeee'}, 'content': [
                    {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Specification',
                                                      'marks': [{'type': 'strong'}]}]}]},
            ]}]},
            {'type': 'mediaSingle', 'content': [{'type': 'media', 'attrs': {
                'type': 'file', 'id': 'image-1', 'collection': '', 'width': 752, 'height': 509}}]},
        ]}
        actual = copy.deepcopy(expected)
        cell = actual['content'][0]['content'][0]['content'][0]
        cell['attrs'] = {}
        del cell['content'][0]['content']
        return expected, actual

    def test_jira_empty_attributes_and_empty_paragraph_are_equivalent_without_mutation(self):
        expected, actual = self.documents()
        before = copy.deepcopy((expected, actual))
        self.assertNotEqual(expected, actual)
        self.assertEqual(normalize_adf_for_comparison(expected), normalize_adf_for_comparison(actual))
        self.assertEqual((expected, actual), before)

    def test_normalized_readback_is_verified_on_first_attempt(self):
        expected, actual = self.documents()
        client = Mock()
        client.read_fields.return_value = {'customfield_123': actual}
        with patch('convert_adf.time.sleep') as sleep, patch('sys.stdout', new_callable=io.StringIO) as output:
            result = verify_field_update(client, 'A-1', 'customfield_123', expected, 'CAN List-changes')
        self.assertTrue(result['matched'])
        self.assertFalse(result['exact_matched'])
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['history'][0]['differences'], [])
        self.assertTrue(result['history'][0]['raw_differences'])
        self.assertIn('[NORMALIZED]', output.getvalue())
        sleep.assert_not_called()
        client.read_fields.assert_called_once()

    def test_real_content_formatting_structure_and_media_changes_are_not_ignored(self):
        expected, base = self.documents()
        for change in ('text', 'mark', 'colspan', 'background', 'row', 'image_id', 'width'):
            with self.subTest(change=change):
                actual = copy.deepcopy(base)
                cells = actual['content'][0]['content'][0]['content']
                text = cells[1]['content'][0]['content'][0]
                media = actual['content'][1]['content'][0]['attrs']
                if change == 'text':
                    text['text'] = 'Changed specification'
                elif change == 'mark':
                    text['marks'] = []
                elif change in ('colspan', 'background'):
                    cells[1]['attrs'][change] = 3 if change == 'colspan' else '#ffffff'
                elif change == 'row':
                    actual['content'][0]['content'] = []
                elif change == 'image_id':
                    media['id'] = 'other-image'
                else:
                    media['width'] = 640
                self.assertNotEqual(normalize_adf_for_comparison(expected), normalize_adf_for_comparison(actual))


if __name__ == '__main__':
    unittest.main()
