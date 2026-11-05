#!/usr/bin/env python3
"""Convert an existing query_runner default-values HTML table into a report."""
import argparse
import html
import re
from urllib.parse import urlsplit
from html.parser import HTMLParser
from pathlib import Path



class QueryTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.cell = None
        self.row = []
        self.headers = None
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.in_row, self.row = True, []
        elif self.in_row and tag in ('th', 'td'):
            self.cell = []
        elif self.cell is not None and tag == 'br':
            self.cell.append('\n')

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('th', 'td') and self.cell is not None:
            self.row.append(''.join(self.cell))
            self.cell = None
        elif tag == 'tr' and self.in_row:
            if self.headers is None and 'default_value_xml' in self.row:
                self.headers = self.row
            elif self.headers and len(self.row) == len(self.headers):
                self.rows.append(dict(zip(self.headers, self.row)))
            self.in_row = False


class MappingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.cell = None
        self.cells = []
        self.mapping = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.in_row, self.cells = True, []
        elif self.in_row and tag in ('td', 'th'):
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.cells.append(''.join(self.cell).strip())
            self.cell = None
        elif tag == 'tr' and self.in_row:
            if len(self.cells) >= 3 and self.cells[0] == 'jira/classic:customField':
                source, target = self.cells[1:3]
                if source.isdigit() and target.isdigit():
                    self.mapping.setdefault('customfield_' + source, set()).add(target)
            self.in_row = False


def read_environment_url(path, environment):
    key = 'JIRA_CLOUD_' + environment + '_URL'
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, value = line.split('=', 1)
        if k.strip() == key:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[key] = value
    url = values.get(key, '').rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f'{path}에 유효한 HTTPS {key} 설정이 필요합니다.')
    return url


def main():
    parser = argparse.ArgumentParser(description='기존 SQL 결과 HTML을 기본값 리포트로 변환')
    parser.add_argument('input', help='기존 SQL 결과 HTML')
    parser.add_argument('output', nargs='?', default='default_values_report.html')
    parser.add_argument('--env', choices=('SANDBOX', 'OPS'), help='생략하면 환경을 선택합니다')
    parser.add_argument('--env-file', default='../env', help='환경 설정 파일')
    parser.add_argument('--mapping', default='../id-mappings-jira-classic-customField.html', help='선택한 Cloud 환경의 필드 ID 매핑 파일')
    args = parser.parse_args()
    environment = args.env
    if not environment:
        print('링크 생성 환경을 선택하세요: 1) Sandbox  2) Cloud Ops')
        while True:
            choice = input('선택 (1 또는 2): ').strip()
            if choice in ('1', '2'):
                environment = 'SANDBOX' if choice == '1' else 'OPS'
                break
    try:
        base_url = read_environment_url(Path(args.env_file), environment)
        mapping = MappingParser()
        mapping.feed(Path(args.mapping).read_text(encoding='utf-8-sig'))
        mapping.close()
        if not mapping.mapping:
            raise ValueError('매핑 파일에 유효한 커스텀 필드 ID 매핑이 없습니다.')
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f'링크 환경: {environment} / 매핑 파일: {args.mapping}')
    print('주의: 매핑 파일이 선택한 Cloud 환경에서 생성된 것인지 확인하세요.')
    source, target = Path(args.input), Path(args.output)
    if source.resolve() == target.resolve():
        parser.error('입력과 출력 파일은 달라야 합니다.')
    data = QueryTableParser()
    with source.open(encoding='utf-8') as f:
        while chunk := f.read(65536):
            data.feed(chunk)
    data.close()
    if not data.headers:
        parser.error('결과 테이블에서 default_value_xml 컬럼을 찾지 못했습니다. 기존 SQL 결과 HTML을 지정하세요.')
    fields = {}
    for row in data.rows:
        field_id = row.get('field_id', '')
        if not field_id:
            parser.error('필드 ID가 없는 행이 있습니다. SQL 결과를 확인하세요.')
        field = fields.setdefault(field_id, {'name': row.get('field_name', ''), 'type': row.get('field_type', ''), 'projects': {}, 'contexts': {}})
        project_key = row.get('project_key', '')
        if project_key:
            field['projects'][project_key] = row.get('project_name', '')
        context_key = (row.get('context_id', ''), row.get('field_configuration_id', ''), row.get('default_value_id', ''), row.get('default_value_xml', ''))
        context = field['contexts'].setdefault(context_key, {'row': row, 'projects': {}})
        if project_key:
            context['projects'][project_key] = row.get('project_name', '')

    def esc(value):
        return html.escape(str(value or ''), quote=True)

    projects = {key for field in fields.values() for key in field['projects']}
    with target.open('w', encoding='utf-8') as f:
        f.write('''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>커스텀 필드 기본값 리포트</title><style>
        *{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:#172b4d;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}main{width:90%;max-width:none;margin:auto;padding:36px 0}h1{margin:0;font-size:28px}p,.sub{color:#5d6e82}.metrics{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}.metric{background:white;border:1px solid #dce3ed;border-radius:10px;padding:10px 16px}.metric strong{font-size:20px;margin-left:8px}.toolbar{margin:20px 0;display:flex;gap:12px;align-items:center;flex-wrap:wrap}select{padding:11px 14px;border:1px solid #c4d0df;border-radius:8px;background:white;color:#172b4d;font:inherit}input{width:min(100%,450px);padding:11px 14px;border:1px solid #c4d0df;border-radius:8px;font:inherit}.panel{overflow:auto;background:white;border:1px solid #dce3ed;border-radius:12px}table{border-collapse:collapse;width:100%;min-width:800px;table-layout:fixed}th:nth-child(1){width:24%}th:nth-child(2){width:20%}th:nth-child(3){width:56%}td{overflow-wrap:anywhere}th{text-align:left;background:#edf2f8;color:#4b5e76;font-size:13px}th,td{padding:14px 16px;border-bottom:1px solid #e7ecf2;vertical-align:top}.sub{display:block;font-size:12px;overflow-wrap:anywhere}.project{display:inline-block;background:#e8f1ff;color:#1756a9;border-radius:5px;padding:2px 7px;margin:2px;font-size:12px}details summary{cursor:pointer;color:#1557a4;font-weight:600}.context{padding:10px 0;border-bottom:1px solid #e7ecf2}.context:last-child{border:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:#f4f6fa;padding:12px;border-radius:7px;font:12px/1.5 monospace}tr[hidden]{display:none}
        </style></head><body><main><h1>커스텀 필드 기본값 리포트</h1><p>HTML이 포함된 기본값 후보를 필드별로 묶었습니다. 프로젝트 목록은 조회된 컨텍스트의 적용 후보이며, 실제 필드 사용 여부를 뜻하지 않습니다.</p>''')
        f.write(f'<div class="metrics"><div class="metric">필드<strong>{len(fields):,}</strong></div><div class="metric">프로젝트<strong>{len(projects):,}</strong></div><div class="metric">원본 결과<strong>{len(data.rows):,}</strong></div></div>')
        f.write('''<div class="toolbar"><input id="search" type="search" placeholder="필드 이름, ID, 프로젝트 검색" aria-label="필드 또는 프로젝트 검색"><label for="sort">정렬</label><select id="sort"><option value="field-asc">커스텀 필드 이름 ↑</option><option value="field-desc">커스텀 필드 이름 ↓</option><option value="projects-asc">프로젝트 수 ↑</option><option value="projects-desc">프로젝트 수 ↓</option></select><span id="visible"></span></div><div class="panel"><table><thead><tr><th>커스텀 필드</th><th>대상 프로젝트</th><th>컨텍스트 및 기본값</th></tr></thead><tbody>''')
        for field_id, field in sorted(fields.items()):
            projects_sorted = sorted(field['projects'].items())
            targets = mapping.mapping.get(field_id, set())
            cloud_id = next(iter(targets)) if len(targets) == 1 else None
            field_url = base_url + '/secure/admin/ConfigureCustomField!default.jspa?customFieldId=' + cloud_id if cloud_id else None
            search = ' '.join([field_id, field['name'], field['type']] + [x for pair in projects_sorted for x in pair]).lower()
            field_title = f'<a href="{esc(field_url)}" target="_blank" rel="noopener noreferrer">{esc(field["name"])}</a>' if field_url else esc(field['name'])
            mapping_text = 'Cloud: customfield_' + cloud_id if cloud_id else ('Cloud 매핑 중복: 링크 없음' if targets else 'Cloud 매핑 없음: 링크 없음')
            f.write(f'<tr class="result" data-search="{esc(search)}" data-field-name="{esc(field["name"])}" data-field-id="{esc(field_id)}" data-project-count="{len(projects_sorted)}"><td><strong>{field_title}</strong><span class="sub">DC: {esc(field_id)}</span><span class="sub">{esc(mapping_text)}</span><span class="sub">{esc(field["type"])}</span></td><td><strong>{len(projects_sorted)}개 프로젝트</strong><br>')
            for key, name in projects_sorted:
                f.write(f'<span class="project" title="{esc(name)}">{esc(key)}</span>')
            f.write(f'</td><td><details><summary>{len(field["contexts"])}개 컨텍스트 / 기본값 보기</summary>')
            for context in field['contexts'].values():
                row = context['row']
                f.write(f'<div class="context"><strong>{esc(row.get("context_name"))}</strong> <span class="sub">{esc(row.get("context_scope"))} · 컨텍스트 ID: {esc(row.get("context_id"))} · 설정 ID: {esc(row.get("field_configuration_id"))} · 기본값 ID: {esc(row.get("default_value_id"))}</span><span class="sub">적용 대상: {esc(", ".join(sorted(context["projects"])))}</span><details><summary>원본 XML 보기</summary><pre>{esc(row.get("default_value_xml"))}</pre></details></div>')
            f.write('</details></td></tr>')
        f.write('''</tbody></table></div><script>const q=document.getElementById("search"),sort=document.getElementById("sort"),body=document.querySelector("tbody"),rows=[...document.querySelectorAll("tr.result")],v=document.getElementById("visible"),collator=new Intl.Collator("ko",{numeric:true,sensitivity:"base"});function filter(){let n=0;for(const r of rows){const show=r.dataset.search.includes(q.value.trim().toLowerCase());r.hidden=!show;if(show)n++}v.textContent=n+"개 필드 표시"}function order(){const [kind,direction]=sort.value.split("-"),sign=direction==="desc"?-1:1;rows.sort((a,b)=>{const primary=kind==="projects"?Number(a.dataset.projectCount)-Number(b.dataset.projectCount):collator.compare(a.dataset.fieldName,b.dataset.fieldName);return primary?sign*primary:collator.compare(a.dataset.fieldId,b.dataset.fieldId)});for(const row of rows)body.appendChild(row)}q.addEventListener("input",filter);sort.addEventListener("change",order);order();filter();</script></main></body></html>''')
    print(f'리포트 생성 완료: {target} ({len(fields)}개 필드, 원본 {len(data.rows)}건)')


if __name__ == '__main__':
    main()
