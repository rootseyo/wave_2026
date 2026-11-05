import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from markdown_adf import convert_pipe_tables
from convert_adf import build_parser, process_export_row

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / 'examples/mqtt_pipe_table.txt').read_text().rstrip('\n')


def doc(text=TEXT):
    return {'type': 'doc', 'version': 1, 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}]}


def cell_text(cell):
    return ''.join(n['text'] for n in cell['content'][0]['content'])


class MqttTableTests(unittest.TestCase):
    def test_exact_user_text(self):
        original = doc()
        before = copy.deepcopy(original)
        result, count = convert_pipe_tables(original)
        self.assertEqual(original, before)
        self.assertEqual(count, 1)
        self.assertEqual([n['type'] for n in result['content']], ['paragraph', 'table'])
        self.assertEqual(result['content'][0]['content'][0]['text'], TEXT[:TEXT.index('|*Parameter*|')])
        rows = result['content'][1]['content']
        self.assertEqual(len(rows), 12)
        self.assertEqual([len(r['content']) for r in rows], [4] * 12)
        self.assertEqual([cell_text(c) for c in rows[0]['content']], ['Parameter', 'Description', 'Default Value', 'note'])
        self.assertTrue(all(c['type'] == 'tableHeader' for c in rows[0]['content']))
        self.assertTrue(all(c['type'] == 'tableCell' for r in rows[1:] for c in r['content']))
        self.assertEqual(cell_text(rows[4]['content'][2]), '136,137,159')
        self.assertEqual(cell_text(rows[4]['content'][3]), '')
        self.assertEqual(cell_text(rows[6]['content'][2]), '15')
        self.assertEqual(cell_text(rows[7]['content'][3]), '')
        self.assertEqual(cell_text(rows[-1]['content'][0]), 'MQTT.subscribe.qos')
        self.assertEqual(cell_text(rows[-1]['content'][3]), '')
        self.assertEqual(convert_pipe_tables(result), (result, 0))

    def test_real_line_breaks(self):
        text = TEXT.replace('| |MQTT.', '|\n|MQTT.')
        result, count = convert_pipe_tables(doc(text))
        self.assertEqual(count, 1)
        self.assertEqual(len(result['content'][1]['content']), 12)

    def test_code_fence_untouched(self):
        original = doc('```\n' + TEXT + '\n```')
        self.assertEqual(convert_pipe_tables(original), (original, 0))

    def test_trailing_prose_kept(self):
        result, count = convert_pipe_tables(doc(TEXT + ' End of specification.'))
        self.assertEqual(count, 1)
        self.assertEqual(result['content'][-1]['content'][0]['text'], ' End of specification.')

    def test_cloud_dry_run_and_payload(self):
        client = Mock(base_url='https://cloud.example')
        client.read_fields.return_value = {'description': doc(), 'attachment': []}
        client.write_fields.side_effect = lambda key, fields: client.read_fields.return_value.update(fields)
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(['--issue', 'CFMT-3'])
            row = {'issue_key': 'CFMT-3', 'field_id': 'description', 'content': TEXT,
                   'offset': 0, 'source_file': 'data.html'}
            result = process_export_row(row, args, client, Mock(), Path(directory), {}, {})
            self.assertEqual(result['tables'], 1)
            client.write_fields.assert_not_called()
            preview = json.loads((Path(directory) / 'CFMT-3_description_0.json').read_text())
            self.assertEqual(preview['original_fields']['description'], doc())
            args.real_run = True
            process_export_row(row, args, client, Mock(), Path(directory), {}, {})
            self.assertEqual(client.write_fields.call_args.args, ('CFMT-3', preview['fields']))


if __name__ == '__main__':
    unittest.main()
