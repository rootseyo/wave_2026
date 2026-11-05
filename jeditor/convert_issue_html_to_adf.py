#!/usr/bin/env python3
"""Interactively convert one Jira Cloud issue field containing HTML to Jira wiki text."""
import argparse
import json
import re
import sys
from urllib.parse import quote

from bs4 import BeautifulSoup
from convert_adf import CloudClient, cloud_config, load_environment
from migration_content import color
from html_to_jira_wiki import html_to_jira_wiki


def extract_html(value):
    if isinstance(value, str):
        return value
    # Jira v3 can return literal HTML stored as text inside an ADF document.
    # Reject richer ADF so that links, marks and other content cannot be lost.
    if isinstance(value, dict) and value.get('type') == 'doc':
        parts = []
        for block in value.get('content', []):
            if block.get('type') != 'paragraph':
                raise ValueError('기존 ADF에 문단 외의 콘텐츠가 있어 자동 변환하지 않습니다.')
            chunks = []
            for node in block.get('content', []):
                if node.get('type') == 'text' and not node.get('marks'):
                    chunks.append(node.get('text', ''))
                elif node.get('type') == 'hardBreak':
                    chunks.append('\n')
                else:
                    raise ValueError('기존 ADF에 서식 또는 지원하지 않는 노드가 있어 자동 변환하지 않습니다.')
            parts.append(''.join(chunks))
        return '\n'.join(parts)
    raise ValueError('필드가 비어 있거나 문자열/단순 ADF가 아닙니다.')


def prepare_html(value):
    soup = BeautifulSoup(value, 'html.parser')
    if not soup.find(re.compile(r'^(p|div|span|br|b|strong|i|em|u|s|del|a|ol|ul|li|table|h[1-6]|pre|font|img|blockquote)$', re.I)):
        raise ValueError('HTML 태그를 찾지 못했습니다. 기존 값을 변경하지 않습니다.')
    warnings = []
    for tag in soup.find_all(True):
        if tag.name == 'font' and tag.get('color'):
            tag['style'] = tag.get('style', '') + ';color:' + tag['color']
        if tag.name == 'img':
            warnings.append('이미지 참조는 Wiki 표기로 변환됩니다. Jira에서 이미지 접근 및 표시를 확인하세요.')
        if tag.get('class'):
            warnings.append('CSS 클래스의 스타일은 변환되지 않습니다.')
        declarations = tag.get('style', '').split(';')
        normalized = []
        for declaration in declarations:
            key, sep, val = declaration.partition(':')
            key = key.strip().lower()
            if not sep:
                continue
            if key == 'color' and color(val):
                declaration = 'color:' + color(val)
            normalized.append(declaration)
            if key == 'color' and not color(val):
                warnings.append(f'지원하지 않는 글자 색상: {val.strip()}')
            elif key in ('background', 'background-color') and tag.name not in ('td', 'th'):
                warnings.append('텍스트 배경색은 보존되지 않을 수 있습니다.')
            elif key not in ('color', 'background-color'):
                warnings.append(f'CSS 속성은 보존되지 않을 수 있습니다: {key}')
        if tag.has_attr('style'):
            tag['style'] = ';'.join(normalized)
        if tag.name in ('style', 'script'):
            warnings.append(f'<{tag.name}> 콘텐츠는 제외됩니다.')
    return str(soup), list(dict.fromkeys(warnings))


def select_field(client, issue_key, selected):
    response = client.request('GET', '/rest/api/3/issue/' + quote(issue_key, safe=''),
                              params={'fields': '*all', 'expand': 'names,schema'})
    try:
        issue = response.json()
    finally:
        response.close()
    fields, names = issue.get('fields', {}), issue.get('names', {})
    matches = [key for key in fields if key.lower() == selected.lower() or
               names.get(key, '').casefold() == selected.casefold()]
    if len(matches) != 1:
        detail = ', '.join(f'{names.get(k, k)} ({k})' for k in matches)
        raise ValueError('필드를 찾지 못했거나 이름이 중복됩니다. 필드 ID를 입력하세요.' + (' ' + detail if detail else ''))
    field_id = matches[0]
    return field_id, names.get(field_id, field_id), fields[field_id]


def write_wiki(client, issue_key, field_id, wiki):
    response = client.request('PUT', '/rest/api/3/issue/' + quote(issue_key, safe=''),
                              params={'notifyUsers': 'false'},
                              json={'fields': {field_id: wiki}}, accepted_statuses=(400,))
    try:
        if response.status_code == 400:
            try:
                detail = response.json()
                message = json.dumps(detail, ensure_ascii=False, indent=2)
            except ValueError:
                message = response.text[:4000] or '(응답 본문 없음)'
            raise RuntimeError('Jira PUT HTTP 400 상세 응답:\n' + message)
    finally:
        response.close()


def main():
    parser = argparse.ArgumentParser(description='이슈 필드의 HTML을 Jira Wiki 문자열로 변환합니다.')
    parser.add_argument('--env', choices=('SANDBOX', 'OPS'), default='SANDBOX')
    parser.add_argument('--issue', help='Jira 이슈 키')
    parser.add_argument('--field', help='필드 이름 또는 ID')
    parser.add_argument('--dry-run', action='store_true', help='변환 결과만 출력하고 저장하지 않음')
    args = parser.parse_args()
    issue_key = args.issue or input('이슈 번호 (예: PROJ-123): ').strip()
    field = args.field or input('필드 이름 또는 ID: ').strip()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*-\d+', issue_key) or not field:
        raise ValueError('이슈 번호 또는 필드 이름이 올바르지 않습니다.')
    load_environment()
    url, auth = cloud_config(args.env)
    # This test script connects without a custom CA or certificate verification.
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    client = CloudClient(url, auth, verify=False)
    try:
        field_id, name, value = select_field(client, issue_key, field)
        response = client.request('GET', '/rest/api/3/issue/' + quote(issue_key, safe='') + '/editmeta')
        try:
            editable = response.json().get('fields', {})
        finally:
            response.close()
        if field_id not in editable:
            raise ValueError(f'{name} ({field_id}) 필드를 이 이슈에서 수정할 수 없습니다. 권한과 편집 화면 설정을 확인하세요.')
        schema = editable[field_id].get('schema', {})
        print('필드 스키마:', json.dumps(schema, ensure_ascii=False))
        raw_html = extract_html(value)
        html, warnings = prepare_html(raw_html)
        wiki, conversion_warnings = html_to_jira_wiki(html)
        warnings.extend(conversion_warnings)
        if not wiki:
            raise ValueError('변환된 Wiki 문자열이 비어 있어 저장을 중단합니다.')
        print(f'대상: {issue_key} / {name} ({field_id}) / {url}')
        for warning in dict.fromkeys(warnings):
            print('주의:', warning)
        print('변환된 Jira Wiki 문자열:')
        print(wiki)
        if args.dry_run:
            print('Dry run: 저장하지 않았습니다.')
            return 0
        if input('이 필드를 변환 결과로 덮어쓰려면 YES를 입력하세요: ').strip() != 'YES':
            print('취소했습니다.')
            return 0
        # Check immediately before writing to reduce accidental overwrites.
        if client.read_fields(issue_key, [field_id]).get(field_id) != value:
            raise ValueError('조회 이후 필드 값이 변경되어 저장을 중단했습니다.')
        write_wiki(client, issue_key, field_id, wiki)
        actual = client.read_fields(issue_key, [field_id]).get(field_id)
        if actual != wiki:
            print('저장 후 조회 결과가 변환 결과와 다릅니다. Jira에서 직접 확인하세요.', file=sys.stderr)
            return 2
        print('저장 및 재조회 확인 완료.')
        return 0
    finally:
        client.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError) as error:
        print(f'오류: {error}', file=sys.stderr)
        sys.exit(1)
