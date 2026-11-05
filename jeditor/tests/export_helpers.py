import html
from pathlib import Path

HEADERS = ['project_key', 'project_name', 'issue_key', 'issue_status', 'field_id', 'field_name', 'detection_type', 'content', 'migration_status']


def write_export(path, rows):
    body = ['<!DOCTYPE html><html><body><table><tr>' + ''.join('<th>' + h + '</th>' for h in HEADERS) + '</tr>']
    for row in rows:
        cells = []
        for column in HEADERS:
            value = html.escape(str(row.get(column, '')))
            if column == 'content':
                value = value.replace('\n', '<br>\n')
            cells.append('<td>' + value + '</td>')
        body.append('<tr>' + ''.join(cells) + '</tr>')
    Path(path).write_text('\n'.join(body) + '</table></body></html>', encoding='utf-8')
    return Path(path)


def source_row(**overrides):
    row = {'project_key': 'AAOSFRWDEV', 'project_name': 'Connect SW dev', 'issue_key': 'AAOSFRWDEV-543',
           'issue_status': 'Resolved', 'field_id': 'description', 'field_name': 'Description',
           'detection_type': 'Attachment', 'content': 'h2. Spec\n|*A*|*B*|\n|one|two|',
           'migration_status': 'Success (Already Migrated)'}
    row.update(overrides)
    return row
