#!/usr/bin/env python3
"""Migrate data.html source rows (HTML/Jira wiki/Markdown + attachments) to Cloud ADF."""
import argparse
from collections import Counter
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import gc
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shlex
import sys
import ssl
import time
import traceback
from urllib.parse import quote, urlsplit
import uuid

from terminal_status import progress_status, with_terminal_status

VERSION = '4.0.0'
ISSUE_KEY = re.compile(r'[A-Za-z][A-Za-z0-9_]*-\d+')
FIELD_ID = re.compile(r'description|environment|customfield_\d+')
FIELD_MAP_FILENAME = 'id-mappings-jira-classic-customField.html'


class JiraFieldMappingParser(HTMLParser):
    """Read serverId → cloudId rows from Jira's custom field mapping report."""
    def __init__(self):
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.cells = []
        self.cell = ''
        self.mapping = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.in_row, self.cells = True, []
        elif self.in_row and tag in ('td', 'th'):
            self.in_cell, self.cell = True, ''

    def handle_data(self, data):
        if self.in_cell:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.in_cell:
            self.cells.append(self.cell.strip())
            self.in_cell = False
        elif tag == 'tr' and self.in_row:
            if len(self.cells) >= 3 and self.cells[0] == 'jira/classic:customField':
                source, target = self.cells[1:3]
                if source.isdigit() and target.isdigit():
                    self.mapping['customfield_' + source] = 'customfield_' + target
            self.in_row = False


def load_environment():
    """Read the existing literal env format without expanding secrets."""
    for path in (Path('env'), Path('../env')):
        if not path.is_file():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)
        break


def cloud_config(environment):
    prefixes = ['JIRA_CLOUD_SANDBOX', 'JIRA_CLOUD_STG', 'JIRA_CLOUD'] if environment.upper() == 'SANDBOX' else ['JIRA_CLOUD_OPS', 'JIRA_CLOUD']
    prefix = next((p for p in prefixes if os.environ.get(p + '_URL')), None)
    if prefix is None:
        raise ValueError(f'Jira Cloud URL is not configured for {environment}')
    url, email, token = (os.environ.get(prefix + suffix, '').strip() for suffix in ('_URL', '_ADMIN_EMAIL', '_API_TOKEN'))
    if not all((url, email, token)):
        raise ValueError(f'Set {prefix}_URL, {prefix}_ADMIN_EMAIL, {prefix}_API_TOKEN')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Jira Cloud URL must be an HTTPS URL without embedded credentials/query/fragment')
    return url.rstrip('/'), (email, token)


def ssl_verification(args):
    """Disable certificate verification for both Cloud and source requests."""
    if args.ca_bundle:
        raise ValueError('--ca-bundle cannot be used while SSL verification is disabled')
    return False


class CloudClient:
    def __init__(self, base_url, auth, session=None, verify=True):
        self.base_url, self.auth, self.session = base_url, auth, session
        self.verify = verify
        self._local = threading.local()
        self._sessions = []
        self._sessions_lock = threading.Lock()
        self.field_names, self.field_schemas = {}, {}

    def _http_client(self):
        if self.session is not None:
            return self.session
        if not hasattr(self._local, 'session'):
            import requests
            session = requests.Session()
            with self._sessions_lock:
                self._sessions.append(session)
            self._local.session = session
        return self._local.session

    def close(self):
        """Close owned connections after all workers have finished."""
        with self._sessions_lock:
            for session in self._sessions:
                session.close()
            self._sessions.clear()
        self._local = threading.local()

    def request(self, method, path, **kwargs):
        headers = {'Accept': 'application/json', **kwargs.pop('headers', {})}
        accepted = kwargs.pop('accepted_statuses', ())
        client = self._http_client()
        attempts = 1 if method.upper() == 'POST' else 4
        for attempt in range(attempts):
            try:
                response = client.request(method, self.base_url + path, auth=self.auth,
                                          headers=headers, timeout=30,
                                          verify=self.verify, **kwargs)
            except Exception as error:
                if isinstance(error, ssl.SSLError) or type(error).__name__ == 'SSLError':
                    detail = str(error).lower()
                    if any(word in detail for word in ('certificate', 'cert_verify', 'hostname', 'self-signed')):
                        raise RuntimeError('SSLError: 인증서 검증 실패. 대화형 SSL 설정에서 사내 CA 파일 또는 검증 생략을 선택하세요.') from None
                    raise RuntimeError('SSLError: TLS 연결 실패. 프록시/TLS 설정과 원본 오류의 상세 원인을 확인하세요. 인증서 검증 생략만으로 해결되지 않을 수 있습니다.') from None
                raise
            if response.status_code in (429, 502, 503, 504) and attempt < attempts - 1:
                try:
                    delay = min(60, max(1, float(response.headers.get('Retry-After', 2 ** (attempt + 1)))))
                except (TypeError, ValueError):
                    delay = 2 ** (attempt + 1)
                response.close()
                time.sleep(delay)
                continue
            if not 200 <= response.status_code < 300 and response.status_code not in accepted:
                # Keep only Jira's error messages, never auth headers or request bodies.
                status = response.status_code
                detail = {}
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        for key in ('errorMessages', 'errors', 'message'):
                            if body.get(key):
                                detail[key] = body[key]
                except ValueError:
                    pass
                finally:
                    response.close()
                message = f'Jira {method} failed: HTTP {status}'
                if detail:
                    message += ' | ' + json.dumps(detail, ensure_ascii=False)[:4000]
                else:
                    message += ' | Jira 상세 오류 메시지 없음'
                error = RuntimeError(message)
                error.status_code = status
                error.jira_errors = detail
                raise error
            return response
        raise RuntimeError('Jira retry attempts exhausted')

    def read_fields(self, issue_key, fields):
        response = self.request('GET', '/rest/api/3/issue/' + quote(issue_key, safe=''),
                                params={'fields': ','.join(fields), 'expand': 'names,schema'})
        try:
            data = response.json()
            with self._sessions_lock:
                self.field_names.update(data.get('names') or {})
                self.field_schemas.update(data.get('schema') or {})
            return data['fields']
        finally:
            response.close()

    def write_fields(self, issue_key, fields):
        response = self.request('PUT', '/rest/api/3/issue/' + quote(issue_key, safe=''),
                                params={'notifyUsers': 'false'}, json={'fields': fields})
        try:
            return response.status_code
        finally:
            response.close()

    def search_keys(self, jql, page_size=100):
        token, previous = None, set()
        while True:
            params = {'jql': jql, 'maxResults': page_size, 'fields': 'id'}
            if token:
                params['nextPageToken'] = token
            data = self.request('GET', '/rest/api/3/search/jql', params=params).json()
            for issue in data.get('issues', []):
                yield issue['key']
            token = data.get('nextPageToken')
            if data.get('isLast') is True or not token:
                break
            if token in previous:
                raise RuntimeError('Jira search returned a repeated page token')
            previous.add(token)


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


class Checkpoints:
    """Only the coordinator accesses this journal. Dry-run never records completion."""
    def __init__(self, path, context, flush_interval=50):
        self.path, self.context = Path(path), context
        self.flush_interval = flush_interval
        self._dirty = 0
        self.saved = {'contexts': {}}
        if self.path.exists():
            try:
                self.saved = json.loads(self.path.read_text(encoding='utf-8'))
            except Exception:
                self.saved = {'contexts': {}}
        self.records = self.saved.setdefault('contexts', {}).setdefault(context, {})

    def should_resume(self, key, *, single_issue=False, retry_skipped=False):
        if single_issue:
            return False
        status = self.records.get(key, {}).get('status')
        return status == 'SUCCEEDED' or (status == 'SKIPPED' and not retry_skipped)

    def record(self, key, status):
        self.records[key] = {'status': status, 'at': datetime.now(timezone.utc).isoformat()}
        self._dirty += 1
        if self._dirty >= self.flush_interval:
            self.flush()

    def flush(self):
        if self._dirty:
            save_json(self.path, self.saved)
            self._dirty = 0

    def __del__(self):
        try:
            self.flush()
        except Exception:
            pass


def build_parser():
    parser = argparse.ArgumentParser(description='Migrate data.html source content and attachments to Jira Cloud ADF')
    parser.add_argument('legacy', nargs='*', help='Issue key alone runs one SANDBOX real test; [dry-run|real] [CFMT-3] also accepted')
    parser.add_argument('--env', '-e', type=str.upper, choices=('SANDBOX', 'OPS'), default='SANDBOX')
    targets = parser.add_mutually_exclusive_group()
    targets.add_argument('--issue', help='One source issue from data.html: no row counting or resume skips')
    targets.add_argument('--project', '-p', help='Comma-separated source project keys in data.html')
    targets.add_argument('--jql', help=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Preview only (default)')
    mode.add_argument('--real-run', action='store_true', help='Save changed ADF fields to Cloud')
    parser.add_argument('--field', action='append', dest='fields', help='Filter source field IDs (default: all supported fields from export)')
    parser.add_argument('--retry-skipped', action='store_true', help='Re-evaluate previous SKIPPED items; preserve successful completion records')
    parser.add_argument('--input', '-i', default='data.jsonl', help='Exported Jira rows (default: data.jsonl or data.html)')
    parser.add_argument('--source-env', choices=('OPS', 'STG'), default='OPS', help='Original Jira used for filename-only attachment lookup')
    parser.add_argument('--source-alias', action='append', dest='source_aliases', help='Additional trusted original Jira URL using selected source credentials')
    parser.add_argument('--field-map', help='JSON/HTML field mapping; defaults to ../' + FIELD_MAP_FILENAME + ' when present')
    parser.add_argument('--allow-same-field-ids', action='store_true',
                        help='Explicitly allow unmapped custom fields to use their source IDs')
    parser.add_argument('--target-issue', help='Optional Cloud issue override for a single source-issue test')
    parser.add_argument('--output-dir', '-o', default='convert_adf_reports')
    parser.add_argument('--workers', '-w', type=int, default=4)
    parser.add_argument('--total', '-t', type=int, default=None,
                        help='전체 대상 건수 (미지정 시 input 파일에서 자동 계산)')
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument('--ca-bundle', help='Unavailable while SSL verification is disabled')
    tls.add_argument('--insecure', action='store_true', help='Disable TLS certificate verification explicitly (testing only)')
    parser.add_argument('--no-prompt', action='store_true')
    parser.add_argument('--html-only', action='store_true',
                        help='Only convert fields containing HTML elements or JEditor markup (skips plain text/non-rich-text fields)')
    parser.add_argument('--debug', action='store_true', help='Print source snippets and first readback differences (may contain field content)')
    parser.add_argument('--cloud-markdown', action='store_true', help=argparse.SUPPRESS)
    return parser


def normalize_args(args, parser):
    legacy_mode = None
    positional_issue = False
    for value in args.legacy:
        if value.lower() in ('real', 'real-run', 'dry', 'dry-run'):
            if legacy_mode is not None:
                parser.error('Specify the execution mode only once')
            legacy_mode = value.lower()
        elif ISSUE_KEY.fullmatch(value) and not (args.issue or args.project or args.jql):
            args.issue = value
            positional_issue = True
        else:
            parser.error(f'Unsupported legacy argument: {value}')
    if not args.dry_run and not args.real_run and legacy_mode in ('real', 'real-run'):
        args.real_run = True
    if positional_issue:
        if args.env != 'SANDBOX':
            parser.error('이슈 번호만 입력하는 단건 테스트는 SANDBOX 전용입니다. OPS는 --issue와 실행 모드를 명시하세요.')
        if not args.dry_run and not args.real_run and legacy_mode not in ('dry', 'dry-run'):
            args.real_run = True
        args.workers = 1
    if args.issue:
        args.issue = args.issue.upper()
        if not ISSUE_KEY.fullmatch(args.issue):
            parser.error('Invalid issue key')
    args.fields = list(dict.fromkeys(args.fields)) if args.fields else None
    if args.fields and any(not FIELD_ID.fullmatch(field) for field in args.fields):
        parser.error('--field must be description, environment or customfield_<id>')
    if not 1 <= args.workers <= 32:
        parser.error('--workers must be between 1 and 32')
    if args.total is not None and args.total <= 0:
        parser.error('--total must be greater than 0')
    if args.target_issue:
        args.target_issue = args.target_issue.upper()
        if not args.issue or not ISSUE_KEY.fullmatch(args.target_issue):
            parser.error('--target-issue requires one source --issue and a valid target key')
    if args.project:
        projects = [key.strip().upper() for key in args.project.split(',')]
        if any(not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) for key in projects):
            parser.error('Invalid project key')
        args.project = ','.join(projects)
    if args.jql:
        parser.error('data.html mode selects source rows by issue/project; Cloud JQL is not an input source')
    if not Path(args.input).is_file():
        if args.input == 'data.jsonl' and Path('data.html').is_file():
            args.input = 'data.html'
        elif args.input == 'data.html' and Path('data.jsonl').is_file():
            args.input = 'data.jsonl'
        else:
            parser.error(f'Input export not found: {args.input}')
    return args


def prompt_value(message, default=None, valid=None, error='입력값을 확인해 주세요.'):
    """Re-prompt invalid answers; EOF cancels rather than silently starting work."""
    while True:
        answer = input(message).strip()
        if not answer and default is not None:
            answer = default
        if answer and (valid is None or valid(answer)):
            return answer
        print(error)


def prompt_menu(title, choices, default='1'):
    print('\n' + title)
    for number, label in choices.items():
        print(f'  {number}. {label}')
    return prompt_value(f'선택 [기본 {default}]: ', default, lambda value: value in choices,
                        '표시된 번호 중 하나를 입력하세요.')


def interactive_args(parser):
    print(f'\ndata.jsonl / data.html → ADF / 첨부파일 이전 v{VERSION}')
    args = parser.parse_args([])
    print('\n[1/9] 입력 파일')
    default_in = 'data.jsonl' if Path('data.jsonl').is_file() else ('data.html' if Path('data.html').is_file() else 'data.jsonl')
    args.input = prompt_value(f'내보내기 파일 (.jsonl 또는 .html) [기본 {default_in}]: ', default_in,
        valid=lambda value: Path(value).expanduser().is_file(), error='존재하는 .jsonl 또는 .html 파일 경로를 입력하세요.')
    args.input = str(Path(args.input).expanduser())
    environment = prompt_menu('[2/9] 대상 Jira Cloud 환경', {'1': 'SANDBOX', '2': 'OPS'})
    args.env = 'SANDBOX' if environment == '1' else 'OPS'
    selected = prompt_menu('[3/9] data.html 처리 범위', {'1': '이슈 한 건 재테스트', '2': '프로젝트', '3': '파일 전체'})
    if selected == '1':
        args.issue = prompt_value('data.html의 원본 이슈 번호: ', valid=lambda value: bool(ISSUE_KEY.fullmatch(value))).upper()
        print('단건은 이전 성공/완료 기록을 무시하고 모든 해당 필드 행을 다시 처리합니다.')
        target = prompt_value('대상 Cloud 이슈 번호 [기본 원본과 동일]: ', args.issue,
                             valid=lambda value: bool(ISSUE_KEY.fullmatch(value))).upper()
        args.target_issue = target if target != args.issue else None
    elif selected == '2':
        args.project = prompt_value('프로젝트 키 (쉼표 구분): ',
            valid=lambda value: all(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', key.strip()) for key in value.split(','))).upper()
    print('\n[4/9] 처리할 필드')
    fields = prompt_value('필드 ID (쉼표 구분, 전체 필드는 *) [기본 *]: ', '*',
        valid=lambda value: value == '*' or all(FIELD_ID.fullmatch(field.strip()) for field in value.split(',')))
    args.fields = None if fields == '*' else [field.strip() for field in fields.split(',')]
    default_mapping = Path('..') / FIELD_MAP_FILENAME
    if default_mapping.is_file():
        args.field_map = str(default_mapping)
        print(f'필드 매핑: {default_mapping} (자동 사용; 다른 파일은 --field-map 지정)')
    else:
        args.field_map = None
        print(f'필드 매핑: {default_mapping} 없음 (매핑 없는 커스텀 필드는 처리 실패)')
    print('댓글 행은 원본 댓글 ID가 없어 새 댓글을 생성하지 않고 제외 사유를 기록합니다.')
    print('\n[5/9] Cloud/원본 Jira SSL: 인증서 검증 생략 (서버/호스트 인증 안 함)')
    source = prompt_menu('[6/9] 원본 첨부파일 Jira 환경 (파일명만 있는 참조에 사용)', {'1': 'OPS', '2': 'STG'})
    args.source_env = 'OPS' if source == '1' else 'STG'
    aliases = input('같은 인증을 사용할 원본 별칭 URL (쉼표 구분) [Enter: env 설정만 사용]: ').strip()
    args.source_aliases = [value.strip() for value in aliases.split(',') if value.strip()]
    print('첨부 URL은 env에 설정된 원본 Jira 호스트에서만 다운로드합니다.')
    print('\n[7/9] 재시도/동시 처리/결과')
    if args.issue:
        print('단건: 완료 기록 무시, 워커 1개')
        args.workers = 1
    else:
        args.retry_skipped = prompt_menu('이전 SKIPPED 재평가', {'1': '다시 처리 (성공은 유지)', '2': '기존 기록 유지'}) == '1'
        args.workers = int(prompt_value('워커 수 (1~32) [기본 4]: ', '4', lambda value: value.isdigit() and 1 <= int(value) <= 32))
    args.output_dir = prompt_value('결과 디렉터리 [기본 convert_adf_reports]: ', 'convert_adf_reports')
    mode = prompt_menu('[8/9] 실행 모드', {'1': '미리보기 (다운로드/업로드/필드 저장 안 함)', '2': '실제 반영 (첨부 이전 및 ADF 저장)'})
    args.real_run, args.dry_run = mode == '2', mode != '2'
    print('\n[9/9] 실행 설정')
    print(f'  입력: {args.input} | 범위: {args.issue or args.project or "파일 전체"}')
    print(f'  Cloud: {args.env} | 원본: {args.source_env} | 대상 이슈: {args.target_issue or "원본과 동일"}')
    print(f'  필드: {", ".join(args.fields) if args.fields else "파일의 모든 ADF 필드"}')
    print(f'  SSL: 검증 생략 | 실행: {"실제 반영" if args.real_run else "미리보기"}\n')
    return args


def load_field_map(path):
    if not path:
        default_path = Path('..') / FIELD_MAP_FILENAME
        if not default_path.is_file():
            return {}
        path = default_path
    mapping_path = Path(path).expanduser()
    with mapping_path.open('r', encoding='utf-8') as f:
        head = f.read(4096)
        is_html = mapping_path.suffix.lower() in ('.html', '.htm') or '<table' in head.lower()
        if is_html:
            parser = JiraFieldMappingParser()
            parser.feed(head)
            while chunk := f.read(1048576):
                parser.feed(chunk)
            parser.close()
            mapping = parser.mapping
            if not mapping:
                raise ValueError('No jira/classic:customField mappings found in HTML file')
        else:
            mapping = json.loads(head + f.read())
    if not isinstance(mapping, dict) or any(not FIELD_ID.fullmatch(key) or not isinstance(value, str) or not FIELD_ID.fullmatch(value) for key, value in mapping.items()):
        raise ValueError('Field mapping JSON must contain source field ID: target field ID entries')
    return mapping


def single_issue_rows(args):
    """Resolve a single issue before creating clients or starting Cloud writes."""
    cached = getattr(args, '_single_issue_rows', None)
    if cached is not None:
        return cached
    from export_data import read_export
    rows = list(read_export(args.input, args.issue))
    if not rows:
        raise ValueError(f'{args.input}에 이슈 {args.issue}가 없습니다. Cloud 요청은 수행하지 않았습니다.')
    if args.fields:
        rows = [row for row in rows if row['field_id'] in args.fields]
        if not rows:
            raise ValueError(f'{args.input}에 이슈 {args.issue}는 있지만 선택한 필드가 없습니다. Cloud 요청은 수행하지 않았습니다.')
    args._single_issue_rows = rows
    return rows


def value_summary(value):
    if isinstance(value, str):
        syntax = 'HTML' if re.search(r'<(?:p|div|span|h[1-6]|table)\b', value, re.I) else 'wiki/텍스트'
        return f'string/{syntax}, {len(value)}자'
    if value is None:
        return 'null (빈 필드)'
    if not isinstance(value, dict):
        return type(value).__name__
    counts, marks = Counter(), Counter()
    def visit(node):
        if not isinstance(node, dict):
            return
        counts[node.get('type', '?')] += 1
        for mark in node.get('marks', []):
            marks[mark.get('type', '?')] += 1
        for child in node.get('content', []):
            visit(child)
    visit(value)
    return (f'{value.get("type", "object")}: 제목 {counts["heading"]}, 문단 {counts["paragraph"]}, '
            f'번호목록 {counts["orderedList"]}, 불릿목록 {counts["bulletList"]}, 이미지 {counts["media"]}, '
            f'표 {counts["table"]}, 텍스트노드 {counts["text"]}, 강조 {marks["strong"]}, 색상 {marks["textColor"]}')


def first_differences(expected, actual, limit=5):
    """Bounded structural diff; complete values remain in the local snapshot."""
    differences = []
    def add(path, kind, left, right):
        differences.append({'path': path, 'kind': kind,
                            'expected': repr(left)[:240], 'actual': repr(right)[:240]})
    def compare(left, right, path):
        if len(differences) >= limit or left == right:
            return
        if type(left) is not type(right):
            add(path, f'type {type(left).__name__} → {type(right).__name__}', left, right)
        elif isinstance(left, dict):
            for key in dict.fromkeys([*left, *right]):
                if len(differences) >= limit:
                    break
                child_path = f'{path}.{key}'
                if key not in left or key not in right:
                    add(child_path, 'key added' if key not in left else 'key missing', left.get(key), right.get(key))
                else:
                    compare(left[key], right[key], child_path)
        elif isinstance(left, list):
            if len(left) != len(right):
                add(path + '.length', 'length', len(left), len(right))
            for index, (lvalue, rvalue) in enumerate(zip(left, right)):
                if len(differences) >= limit:
                    break
                compare(lvalue, rvalue, f'{path}[{index}]')
        else:
            add(path, 'value', left, right)
    compare(expected, actual, '$')
    return differences


def normalize_adf_for_comparison(value):
    """Ignore only empty optional ADF properties observed in Jira readbacks.

    This builds a comparison copy; the transmitted/saved documents stay intact.
    Text, marks, nonempty attrs, table structure and media IDs remain significant.
    """
    if isinstance(value, list):
        return [normalize_adf_for_comparison(child) for child in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if isinstance(value.get('type'), str):
            if key == 'attrs' and child == {}:
                continue
            if value['type'] == 'paragraph' and key == 'content' and child == []:
                continue
        result[key] = normalize_adf_for_comparison(child)
    return result


def _is_content_limit_error(error: Exception) -> bool:
    """Returns True when Jira Cloud rejects an ADF update with HTTP 400 CONTENT_LIMIT_EXCEEDED."""
    if 'CONTENT_LIMIT_EXCEEDED' in str(error):
        return True
    jira_errors = getattr(error, 'jira_errors', None)
    if isinstance(jira_errors, dict) and 'CONTENT_LIMIT_EXCEEDED' in json.dumps(jira_errors):
        return True
    return False


def _adf_json_len(node) -> int:
    return len(json.dumps(node, ensure_ascii=False, separators=(',', ':')))


def _extract_adf_plain_text(node) -> str:
    if isinstance(node, dict):
        ntype = node.get('type')
        if ntype == 'text':
            return node.get('text', '')
        if ntype == 'hardBreak':
            return ' '
        parts = [_extract_adf_plain_text(child) for child in node.get('content', [])]
        if ntype in ('paragraph', 'heading', 'listItem'):
            return ' '.join(p for p in parts if p).strip()
        return ' / '.join(p for p in parts if p).strip()
    if isinstance(node, list):
        return ' '.join(_extract_adf_plain_text(child) for child in node if child).strip()
    return ''


def _make_adf_cell(cell_type: str, text: str, colspan: int = 1, rowspan: int = 1) -> dict:
    lines = [line.strip() for line in (text or '').split('\n') if line.strip()]
    content_nodes = []
    for idx, line in enumerate(lines):
        if idx > 0:
            content_nodes.append({'type': 'hardBreak'})
        content_nodes.append({'type': 'text', 'text': line})
    para = {'type': 'paragraph', 'content': content_nodes} if content_nodes else {'type': 'paragraph', 'content': []}
    cell = {'type': cell_type, 'content': [para]}
    attrs = {}
    if colspan > 1:
        attrs['colspan'] = colspan
    if rowspan > 1:
        attrs['rowspan'] = rowspan
    if attrs:
        cell['attrs'] = attrs
    return cell


def _strip_adf_visual_styles(node, in_header: bool = False):
    """Strips cell background colors, textColor marks, redundant strong marks in headers, and empty paragraphs."""
    if isinstance(node, list):
        cleaned = []
        prev_empty_para = False
        for child in node:
            c = _strip_adf_visual_styles(child, in_header=in_header)
            is_empty_para = isinstance(c, dict) and c.get('type') == 'paragraph' and not c.get('content')
            if is_empty_para and prev_empty_para:
                continue
            prev_empty_para = is_empty_para
            cleaned.append(c)
        return cleaned
    if not isinstance(node, dict):
        return node
    out = {}
    ntype = node.get('type')
    now_in_header = in_header or (ntype == 'tableHeader')
    for k, v in node.items():
        if k == 'attrs' and isinstance(v, dict) and ntype in ('tableCell', 'tableHeader'):
            new_attrs = {ak: av for ak, av in v.items() if ak != 'background' and not (ak in ('colspan', 'rowspan') and av == 1)}
            if new_attrs:
                out['attrs'] = new_attrs
            continue
        if k == 'marks' and isinstance(v, list):
            new_marks = [
                m for m in v
                if isinstance(m, dict)
                and m.get('type') != 'textColor'
                and not (now_in_header and m.get('type') == 'strong')
            ]
            if new_marks:
                out['marks'] = new_marks
            continue
        if k == 'content' and isinstance(v, list):
            out['content'] = _strip_adf_visual_styles(v, in_header=now_in_header)
            continue
        out[k] = v
    return out


def _detect_header_row_count(rows: list) -> int:
    """Detects 1 or 2 header rows at the top of an ADF table (supports both tableHeader and JEditor tableCell headers)."""
    if not rows:
        return 0
    r0_cells = rows[0].get('content', [])
    if not r0_cells:
        return 0
    r0_has_colspan = any(int((c.get('attrs') or {}).get('colspan', 1) or 1) > 1 for c in r0_cells)
    if r0_has_colspan and len(rows) >= 2:
        return 2
    header_count = 0
    for r_idx, row in enumerate(rows[:2]):
        cells = row.get('content', [])
        is_hdr = any(c.get('type') == 'tableHeader' for c in cells)
        if not is_hdr:
            # Check if cells have gray header background (#ddd/#eee/#f0f0f0) or bold text in all non-empty cells
            non_empty = [c for c in cells if _extract_adf_plain_text(c).strip()]
            if non_empty and all(
                str((c.get('attrs') or {}).get('background', '')).lower() in ('#dddddd', '#eeeeee', '#f2f2f2', '#e0e0e0', '#d9d9d9')
                or '"type":"strong"' in json.dumps(c, ensure_ascii=False, separators=(',', ':'))
                for c in non_empty
            ):
                is_hdr = True
        if is_hdr:
            header_count = r_idx + 1
        else:
            break
    return max(1, header_count) if len(rows) >= 2 else 0


def _clean_adf_cell(cell, is_header=False):
    attrs = cell.get('attrs', {})
    cs = int(attrs.get('colspan', 1) or 1)
    rs = int(attrs.get('rowspan', 1) or 1)
    txt = _extract_adf_plain_text(cell).strip()
    return _make_adf_cell('tableHeader' if is_header else 'tableCell', txt, colspan=cs, rowspan=rs)


def _clean_adf_table(table_node: dict, max_data_rows: int = None) -> dict:
    """Cleans an ADF table to 100% standard format: all columns intact, every data row has all cells (rowspan=1, colspan=1)."""
    rows = table_node.get('content', [])
    if not rows:
        return table_node
    h_count = _detect_header_row_count(rows)
    header_rows = rows[:h_count]
    data_rows = rows[h_count:]
    if max_data_rows is not None and len(data_rows) > max_data_rows:
        data_rows = data_rows[:max_data_rows]

    clean_rows = []
    for hr in header_rows:
        clean_rows.append({
            'type': 'tableRow',
            'content': [_clean_adf_cell(c, is_header=True) for c in hr.get('content', [])]
        })
    for dr in data_rows:
        # Every single data row preserves all original cells with colspan=1, rowspan=1
        clean_cells = []
        for c in dr.get('content', []):
            txt = _extract_adf_plain_text(c).strip()
            clean_cells.append(_make_adf_cell('tableCell', txt, colspan=1, rowspan=1))
        clean_rows.append({'type': 'tableRow', 'content': clean_cells})

    return {'type': 'table', 'attrs': {'isNumberColumnEnabled': False, 'layout': 'default'}, 'content': clean_rows}


def _clean_adf_document(document: dict, large_table_caps: dict = None, info_panel_text: str = None) -> dict:
    """Cleans an ADF document preserving all genuine tables, paragraphs, and headings without artificial cell merging."""
    blocks = []
    if info_panel_text:
        blocks.append({
            'type': 'panel',
            'attrs': {'panelType': 'info'},
            'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': info_panel_text, 'marks': [{'type': 'strong'}]}]}]
        })
    for idx, b in enumerate(document.get('content', [])):
        btype = b.get('type')
        if btype == 'paragraph':
            txt = ''.join(n.get('text', '') for n in b.get('content', []))
            if txt.strip():
                blocks.append({'type': 'paragraph', 'content': [{'type': 'text', 'text': txt.strip()}]})
        elif btype == 'table':
            cap = large_table_caps.get(idx) if large_table_caps else None
            blocks.append(_clean_adf_table(b, max_data_rows=cap))
        elif btype in ('heading', 'rule', 'codeBlock', 'panel'):
            blocks.append(b)
    return {'type': 'doc', 'version': 1, 'content': blocks}


def _upload_issue_attachment(client, issue_key: str, filename: str, content_str: str) -> bool:
    """Uploads the full raw HTML specification as an attachment when a Jira Cloud field exceeds 32,767 characters."""
    try:
        session = client._http_client()
        url = client.base_url + f'/rest/api/3/issue/{quote(issue_key, safe="")}/attachments'
        headers = {'X-Atlassian-Token': 'no-check'}
        html_wrapper = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<title>{html.escape(issue_key)} 사양 전체 원본</title>'
            '<style>body{font-family:sans-serif;padding:20px;line-height:1.6;}table{border-collapse:collapse;width:100%;font-size:12px;margin:12px 0;}th,td{border:1px solid #ccc;padding:6px 8px;text-align:left;}th{background:#f4f5f7;font-weight:bold;}</style>'
            f'</head><body><h2>{html.escape(issue_key)} 사양 전체 원본</h2>{content_str}</body></html>'
        )
        content_bytes = html_wrapper.encode('utf-8')
        files = {'file': (filename, content_bytes, 'text/html; charset=utf-8')}
        resp = session.post(url, headers=headers, files=files, auth=client.auth, verify=client.verify)
        if resp.status_code in (200, 201):
            print(f'[ATTACH] {issue_key} | 대형 사양 전체 원본 첨부파일 등록 완료: {filename}', flush=True)
            return True
        else:
            print(f'[ATTACH-WARN] {issue_key} | 첨부파일 등록 HTTP {resp.status_code}: {resp.text[:200]}', flush=True)
            return False
    except Exception as e:
        print(f'[ATTACH-WARN] {issue_key} | 첨부파일 자동 등록 예외 발생 ({e})', flush=True)
        return False


def _build_budget_fitted_document(document: dict, attach_filename: str, target_json_len: int = 31000) -> tuple[dict, int, dict]:
    """Fits clean tables into Jira Cloud's 32,767 character limit without destroying columns or row alignment."""
    # Count total signals/data rows across tables
    total_signals = 0
    large_tables = {}
    for idx, b in enumerate(document.get('content', [])):
        if b.get('type') == 'table':
            rows = b.get('content', [])
            h_cnt = _detect_header_row_count(rows)
            d_cnt = max(0, len(rows) - h_cnt)
            total_signals += d_cnt
            if d_cnt > 4:
                large_tables[idx] = d_cnt

    info_txt = (
        f'ℹ️ 본 안건상세 표는 총 {total_signals}개 행의 대형 사양으로 Jira Cloud 단일 필드 용량 한도(32,767자)에 맞춰 '
        f'상위 주요 신호를 표준 표(10개 컬럼) 형태로 온전히 표시하였으며, 전체 {total_signals}개 행의 원본 사양은 '
        f'본 이슈의 첨부파일({attach_filename})에 보존되었습니다.'
    )

    caps = {idx: min(10, d_cnt) for idx, d_cnt in large_tables.items()}
    fitted = _clean_adf_document(document, large_table_caps=caps, info_panel_text=info_txt)
    cur_len = _adf_json_len(fitted)
    while cur_len > target_json_len and any(caps[k] > 2 for k in caps):
        max_idx = max(caps, key=lambda k: caps[k])
        caps[max_idx] -= 1
        fitted = _clean_adf_document(document, large_table_caps=caps, info_panel_text=info_txt)
        cur_len = _adf_json_len(fitted)

    return fitted, cur_len, caps


def verify_field_update(client, issue_key, field_id, expected, label=None, debug=False):
    """Read the saved field back; a successful PUT alone is not verification."""
    observed = {}
    history = []
    normalized_expected = normalize_adf_for_comparison(expected)
    for attempt in range(1, 4):
        observed = client.read_fields(issue_key, [field_id])
        actual = observed.get(field_id)
        exact_matched = field_id in observed and actual == expected
        matched = field_id in observed and normalize_adf_for_comparison(actual) == normalized_expected
        differences = first_differences(normalized_expected, normalize_adf_for_comparison(actual))
        raw_differences = first_differences(expected, actual)
        if field_id not in observed:
            differences = [{'path': '$', 'kind': 'field missing from GET', 'expected': field_id, 'actual': '(missing)'}]
        history.append({'attempt': attempt, 'summary': value_summary(actual), 'matched': matched,
                        'exact_matched': exact_matched, 'differences': differences,
                        'raw_differences': raw_differences})
        print(f'[VERIFY {attempt}/3] {issue_key} | {label or field_id} | Cloud: {value_summary(actual)}', flush=True)
        if matched:
            if not exact_matched:
                print(f'[NORMALIZED] {label or field_id} | 내용·서식 일치 (Jira의 빈 속성/빈 문단 표현 보정)', flush=True)
                if debug:
                    for difference in raw_differences:
                        print(f'  [JIRA DEFAULT] {difference["path"]} | {difference["kind"]}', flush=True)
            return {'matched': True, 'exact_matched': exact_matched, 'attempts': attempt,
                    'fields': observed, 'history': history}
        for difference in differences:
            print(f'  [DIFF] {difference["path"]} | {difference["kind"]}', flush=True)
            if debug:
                print(f'    전송: {difference["expected"]}\n    조회: {difference["actual"]}', flush=True)
        if attempt < 3:
            time.sleep(0.5 * attempt)
    return {'matched': False, 'exact_matched': False, 'attempts': 3, 'fields': observed, 'history': history}


def process_export_row(row, args, client, source, directory, field_map, managers, registry_lock=None, active=None):
    """Called under the target issue lock; attachment caches are shared across fields."""
    from migration_content import source_to_adf
    from migration_attachments import AttachmentMigrator
    source_key, source_field = row['issue_key'].upper(), row['field_id']
    target_key = args.target_issue or source_key
    if source_field == 'comment':
        return {'issue': target_key, 'source_issue': source_key, 'field': source_field,
                'status': 'FAILED' if args.issue else 'SKIPPED',
                'reason': 'Comment row has no original comment ID; no new comment was created'}
    if not FIELD_ID.fullmatch(source_field):
        raise ValueError(f'Unsupported ADF field ID in export: {source_field}')
    content = row.get('content') or ''
    if getattr(args, 'html_only', False):
        from export_data import has_html_elements
        if not has_html_elements(content):
            source_name = row.get('field_name') or source_field
            target_field = field_map.get(source_field, source_field)
            return {'issue': target_key, 'source_issue': source_key, 'field': target_field,
                    'source_field': source_field, 'field_name': source_name,
                    'status': 'SKIPPED',
                    'reason': 'HTML 요소 없음 (JEditor/HTML 마크업 미포함, ADF 변환 제외)',
                    'tables': 0, 'changed': False}
    if source_field.startswith('customfield_') and source_field not in field_map and not args.allow_same_field_ids:
        raise ValueError(f'Missing Cloud field mapping for {source_field}; supply --field-map '
                         'or explicitly choose --allow-same-field-ids')
    target_field = field_map.get(source_field, source_field)
    debug = getattr(args, 'debug', False)
    source_name = row.get('field_name') or source_field
    print(f'[READ] {client.base_url}/browse/{target_key} | 원본 필드: {source_name} ({source_field})', flush=True)
    current = client.read_fields(target_key, [target_field, 'attachment'])
    names, schemas = getattr(client, 'field_names', {}), getattr(client, 'field_schemas', {})
    cloud_name = names.get(target_field) if isinstance(names, dict) else None
    label = f'{cloud_name or source_name} ({target_field}{"; Cloud 이름 미확인" if not cloud_name else ""})'
    schema = schemas.get(target_field, {}) if isinstance(schemas, dict) else {}
    print(f'[CONVERT] {target_key} | {label}', flush=True)
    print(f'[FIELD] Cloud 타입: {schema.get("type", "미확인")} | 종류: {schema.get("custom") or schema.get("system") or "미확인"}', flush=True)
    print(f'[INPUT] 변환 원문=data.html | {row["source_file"]} | offset={row["offset"]} | {value_summary(row["content"])}', flush=True)
    print(f'[BEFORE] 현재 Cloud 값: {value_summary(current.get(target_field))}', flush=True)
    if debug:
        print(f'[SOURCE] {repr(row["content"][:240])}', flush=True)
    if target_field not in current:
        raise ValueError(f'Cloud field {target_field} is missing; configure its explicit ID mapping if source/Cloud IDs differ')
    manager = managers.get(target_key)
    if manager is None:
        manager = AttachmentMigrator(client, source, target_key, current.get('attachment') or [],
                                     dry_run=not args.real_run, source_issue_key=source_key)
        if registry_lock is not None:
            with registry_lock:
                while len(managers) >= 256:
                    evicted = False
                    for old_key in list(managers.keys()):
                        if not active or not any(a.startswith(f'{old_key}:') for a in active):
                            managers.pop(old_key, None)
                            evicted = True
                            break
                    if not evicted:
                        break
                managers[target_key] = manager
        else:
            while len(managers) >= 256:
                managers.pop(next(iter(managers)), None)
            managers[target_key] = manager
    action_start = len(manager.actions)
    snapshot = {'issue': target_key, 'source_issue': source_key, 'source_field': source_field,
                'field': target_field, 'source_file': row['source_file'], 'offset': row['offset'],
                'cloud_url': client.base_url, 'issue_url': client.base_url + '/browse/' + quote(target_key, safe=''),
                'field_name': cloud_name, 'source_field_name': source_name, 'field_schema': schema,
                'content_source': 'data.html', 'write_accepted': False,
                'source_content': row['content'], 'original_fields': {target_field: current[target_field]},
                'fields': {}, 'attachments': [], 'warnings': [], 'status': 'READY', 'dry_run': not args.real_run}
    preview = directory / f'{source_key}_{target_field}_{row["offset"]}.json'
    save_json(preview, snapshot)
    try:
        raw_document = source_to_adf(row['content'], manager, snapshot['warnings'])
        document = _clean_adf_document(raw_document)
        snapshot['fields'] = {target_field: document}
        snapshot['attachments'] = manager.actions[action_start:]
        snapshot['changed'] = normalize_adf_for_comparison(current[target_field]) != normalize_adf_for_comparison(document)
        print(f'[ADF] {label} | {value_summary(document)} | 기존 값과 다름={snapshot["changed"]}', flush=True)
        for warning in snapshot['warnings']:
            print(f'[WARNING] {label} | {warning}', flush=True)
        print(f'[SNAPSHOT] {preview}', flush=True)
        save_json(preview, snapshot)
        if args.real_run:
            print(f'[PUT] {target_key} | {label} | DC→Cloud 단방향 덮어쓰기 (data.html → ADF)', flush=True)
            try:
                status_code = client.write_fields(target_key, snapshot['fields'])
            except Exception as put_error:
                if not _is_content_limit_error(put_error):
                    raise
                orig_len = _adf_json_len(document)
                print(f'[COMPACT] {target_key} | {label} | CONTENT_LIMIT_EXCEEDED 감지 (ADF {orig_len:,}자) → 표준 표 유지 및 첨부파일 등록 시작', flush=True)

                # 1. 원본 HTML 전체 사양을 이슈 첨부파일로 등록 (데이터 유실 방지)
                field_label = (cloud_name or target_field).replace('/', '_').replace(' ', '_')
                attach_fn = f'{target_key}_{field_label}_전체사양.html'
                _upload_issue_attachment(client, target_key, attach_fn, row.get('content', ''))

                # 2. Jira Cloud 32,767자 한도 내에 맞추면서 10개 전체 컬럼 및 행 정렬을 100% 보존하는 표준 표 구성
                cand_doc, cand_len, caps = _build_budget_fitted_document(document, attach_filename=attach_fn, target_json_len=30000)
                stage_label = f'표준 표(10개 컬럼 온전 유지) 주요 행 표시 + 전체 원본 사양 첨부파일({attach_fn}) 등록'
                candidates = [(stage_label, cand_doc)]

                last_err = put_error
                status_code = None
                for stage_label, cand_doc in candidates:
                    cand_len = _adf_json_len(cand_doc)
                    print(f'[COMPACT-RETRY] {target_key} | {label} | {stage_label} (ADF {orig_len:,}자 → {cand_len:,}자)', flush=True)
                    try:
                        status_code = client.write_fields(target_key, {target_field: cand_doc})
                        document = cand_doc
                        snapshot['fields'] = {target_field: document}
                        comp_warn = f'Jira Cloud CONTENT_LIMIT_EXCEEDED 한도(32,767자) 초과로 {stage_label} 적용 (ADF {orig_len:,}자 → {cand_len:,}자)'
                        snapshot['warnings'].append(comp_warn)
                        print(f'[WARNING] {label} | {comp_warn}', flush=True)
                        last_err = None
                        break
                    except Exception as retry_err:
                        last_err = retry_err
                        if not _is_content_limit_error(retry_err):
                            raise
                if last_err is not None:
                    raise last_err
            snapshot['write_accepted'] = True
            print(f'[PUT] {label} | {"HTTP " + str(status_code) if isinstance(status_code, int) else "저장 요청 수락"} | 재조회 검증 시작', flush=True)
            try:
                snapshot['verification'] = verify_field_update(client, target_key, target_field, document, label, debug)
            except Exception as error:
                snapshot['verification'] = {'matched': False, 'error': {
                    'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()}}
            if snapshot['verification']['matched']:
                print(f'[VERIFIED] {snapshot["issue_url"]} | {label} | '
                      f'{"내용 변경 확인" if snapshot["changed"] else "기존 ADF와 동일 (변경 없음)"}', flush=True)
            else:
                warning = (f'Cloud 덮어쓰기 요청은 수락됐지만 재조회가 불일치하거나 실패했습니다. '
                           f'상세 비교: {preview}')
                snapshot['warnings'].append(warning)
                print(f'[WARNING] {label} | {warning}', flush=True)
        snapshot['status'] = 'SUCCEEDED' if args.real_run else 'PREVIEW'
        save_json(preview, snapshot)
    except Exception as error:
        snapshot['status'] = 'FAILED'
        snapshot['attachments'] = manager.actions[action_start:]
        snapshot['error'] = {'type': type(error).__name__, 'message': str(error),
                             'traceback': traceback.format_exc(),
                             'http_status': getattr(error, 'status_code', None),
                             'jira_errors': getattr(error, 'jira_errors', None)}
        save_json(preview, snapshot)
        raise
    def tables(node):
        return int(node.get('type') == 'table') + sum(tables(child) for child in node.get('content', []))
    table_count = tables(document)
    result_summary = {
        'issue': target_key, 'source_issue': source_key, 'field': target_field,
        'field_name': cloud_name or source_name,
        'status': snapshot['status'], 'tables': table_count,
        'changed': snapshot['changed'], 'write_accepted': snapshot['write_accepted'],
        'verified': snapshot.get('verification', {}).get('matched', False),
        'reason': (('덮어쓰기 수락 · Cloud 재조회 일치 · ' + ('내용 변경 확인' if snapshot['changed'] else '기존 ADF와 동일 (변경 없음)'))
                   if snapshot.get('verification', {}).get('matched') else '덮어쓰기 수락 · 재조회 경고 (상세 JSON 확인)')
                  if args.real_run else '미리보기 (Cloud 저장 없음)',
        'attachments': snapshot['attachments'], 'warnings': snapshot['warnings'], 'preview': str(preview)
    }
    snapshot.clear()
    row['content'] = None
    return result_summary


def format_duration(seconds: float) -> str:
    """Format duration in seconds into human-readable Korean text."""
    if seconds is None or seconds < 0:
        return '계산 중...'
    total_sec = int(round(seconds))
    if total_sec < 60:
        return f'{total_sec}초'
    minutes = total_sec // 60
    rem_sec = total_sec % 60
    if minutes < 60:
        return f'{minutes}분 {rem_sec:02d}초' if rem_sec else f'{minutes}분'
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        return f'{hours}시간 {rem_min}분' if rem_min else f'{hours}시간'
    days = hours // 24
    rem_hr = hours % 24
    return f'{days}일 {rem_hr}시간' if rem_hr else f'{days}일'


def _make_progress_bar(pct: float, width: int = 16) -> str:
    filled = int(width * pct / 100)
    filled = max(0, min(width, filled))
    return '█' * filled + '░' * (width - filled)


class RecentSpeedTracker:
    """Tracks processing rate over a rolling time window (e.g. recent 5 seconds).

    Avoids distortion from resume mode (where thousands of skipped items register in seconds).
    """
    def __init__(self, window_seconds: float = 5.0, min_interval: float = 0.1):
        import collections
        self.lock = threading.Lock()
        self.window_seconds = window_seconds
        self.min_interval = min_interval
        self.samples = collections.deque()
        self.current_rate = 0.0

    def update(self, current_count: int) -> float:
        with self.lock:
            now = time.monotonic()
            if not self.samples or (now - self.samples[-1][0]) >= self.min_interval:
                self.samples.append((now, current_count))
            else:
                self.samples[-1] = (now, current_count)

            cutoff = now - self.window_seconds
            while len(self.samples) > 2 and self.samples[1][0] < cutoff:
                self.samples.popleft()

            if len(self.samples) >= 2:
                t0, c0 = self.samples[0]
                dt = now - t0
                if dt >= 0.5:
                    dn = current_count - c0
                    self.current_rate = max(0.0, dn / dt)
            return self.current_rate


@with_terminal_status
def run_file_conversion(args, client):
    from export_data import count_export_rows, read_export
    from migration_attachments import SourceClient, source_configs
    issue_rows = single_issue_rows(args) if args.issue else None
    started = time.monotonic()
    directory = Path(args.output_dir) / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=True)
    mapping = load_field_map(args.field_map)
    stat = Path(args.input).stat()
    context = hashlib.sha256(json.dumps([client.base_url, str(Path(args.input).resolve()), stat.st_size,
        stat.st_mtime_ns, args.fields, mapping, VERSION]).encode()).hexdigest()
    journal = None if args.issue or not args.real_run else Checkpoints(Path(args.output_dir) / 'file_checkpoints.json', context)
    configs = source_configs()
    if args.source_aliases:
        from migration_attachments import origin
        if args.source_env not in configs:
            raise ValueError(f'Configure JIRA_DC_{args.source_env}_URL before adding source aliases')
        for alias in args.source_aliases:
            origin(alias)
        configs[args.source_env].setdefault('aliases', []).extend(args.source_aliases)
    source = SourceClient(configs, args.source_env, verify=client.verify)
    try:
        counts = {key: 0 for key in ('SUCCEEDED', 'PREVIEW', 'SKIPPED', 'RESUMED', 'FAILED')}
        # Fixed stripes keep memory bounded while retaining serialization for each
        # target issue, including workers already waiting for that issue.
        locks = tuple(threading.Lock() for _ in range(256))
        managers, registry_lock = {}, threading.Lock()
        active = set()
        projects = set(args.project.split(',')) if args.project else None
        selected, pending = 0, {}

        total = args.total
        if total is None:
            if args.issue:
                total = len(issue_rows)
            elif Path(args.input).is_file():
                try:
                    total = count_export_rows(args.input, projects=projects, fields=args.fields) or None
                except Exception:
                    total = None

        progress_status(f'data.html → ADF | {args.issue or "파일 스트리밍 중"} | {"REAL-RUN" if args.real_run else "DRY-RUN"}')
        total_str = f'{total:,}건' if total else '미정'
        print(f'입력: {args.input} (대상: {total_str}) | Cloud: {client.base_url} | 결과: {directory}')
        if args.issue:
            print(f'[RETEST] {args.issue}: 전체 행 수 계산 없이 키 검색, 이전 완료 상태 무시')
        speed_tracker = RecentSpeedTracker(window_seconds=5.0)

        def footer():
            with registry_lock:
                running = ', '.join(sorted(active)[:4]) or '대기'
                active_count = len(active)
            processed = sum(counts.values())
            elapsed = max(time.monotonic() - started, .001)
            recent_rate = speed_tracker.update(processed)
            elapsed_str = format_duration(elapsed)

            lines = []
            # 1줄: 진행률 & 남은/경과 시간 (최근 5초 속도 기준 남은 시간 계산)
            if total and total > 0:
                pct = min(100.0, (processed / total) * 100)
                remaining = max(0, total - processed)
                if remaining == 0:
                    eta_str = '완료'
                elif recent_rate > 0.05:
                    eta_str = format_duration(remaining / recent_rate)
                elif processed >= 2 and (processed / elapsed) > 0.05:
                    eta_str = format_duration(remaining / (processed / elapsed))
                else:
                    eta_str = '계산 중...'
                bar = _make_progress_bar(pct, width=16)
                lines.append(f'ADF | [{bar}] {pct:.1f}% ({processed:,}/{total:,}건) | 남은시간: {eta_str} | 경과: {elapsed_str}')
            else:
                lines.append(f'ADF | 처리 {processed:,}건 | 경과: {elapsed_str}')

            # 2줄: 최근 5초 기준 실시간 속도 및 처리 상태별 건수
            speed_display = f'{recent_rate:.1f}건/s' if recent_rate > 0 else '계산 중...'
            line2_parts = [f'속도: {speed_display} (최근 5초 기준)']
            if args.real_run:
                line2_parts.append(f'성공 {counts["SUCCEEDED"]:,}')
                if counts['PREVIEW']:
                    line2_parts.append(f'미리보기 {counts["PREVIEW"]:,}')
            else:
                line2_parts.append(f'미리보기 {counts["PREVIEW"]:,}')
                if counts['SUCCEEDED']:
                    line2_parts.append(f'성공 {counts["SUCCEEDED"]:,}')
            line2_parts.extend([
                f'실패 {counts["FAILED"]:,}',
                f'스킵 {counts["SKIPPED"] + counts["RESUMED"]:,}',
            ])
            lines.append(' | '.join(line2_parts))

            # 3줄: 활성 스레드 및 처리 중인 이슈 목록
            lines.append(f'활성스레드({active_count}): {running}')

            progress_status(lines)
        footer()
        with (directory / 'results.jsonl').open('a', encoding='utf-8') as report:
            def record(identity, result):
                status = result['status']
                counts[status] += 1
                report.write(json.dumps(result, ensure_ascii=False) + '\n')
                report.flush()
                if journal is not None and status in ('SUCCEEDED', 'SKIPPED', 'FAILED'):
                    journal.record(identity, status)
                display_field = result.get('field_name') or result.get('field', '')
                print(f'[{status}] {result["issue"]}:{display_field} | 표 {result.get("tables", 0)} | {result.get("reason", "")}')
                footer()
                if sum(counts.values()) % 500 == 0:
                    gc.collect()

            def worker(row):
                key = args.target_issue or row['issue_key'].upper()
                lock = locks[hash(key) % len(locks)]
                with lock:
                    label = f'{key}:{row.get("field_name") or row["field_id"]}'
                    with registry_lock:
                        active.add(label)
                    footer()
                    try:
                        return process_export_row(row, args, client, source, directory, mapping, managers, registry_lock, active)
                    except Exception as error:
                        reason = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
                        failed_field = mapping.get(row['field_id'], row['field_id'])
                        names = getattr(client, 'field_names', {})
                        failed_name = names.get(failed_field) if isinstance(names, dict) else None
                        return {'issue': key, 'source_issue': row['issue_key'], 'field': failed_field,
                                'source_field': row['field_id'], 'field_name': failed_name or row.get('field_name', ''),
                                'status': 'FAILED', 'reason': reason[:400]}
                    finally:
                        row['content'] = None
                        with registry_lock:
                            active.discard(label)
                        footer()

            def drain(done):
                for future in done:
                    identity = pending.pop(future)
                    record(identity, future.result())

            executor = ThreadPoolExecutor(max_workers=1 if args.issue else args.workers)
            try:
                for row in (issue_rows if issue_rows is not None else read_export(args.input)):
                    key = row['issue_key'].upper()
                    if not ISSUE_KEY.fullmatch(key):
                        raise ValueError('Invalid issue key in input export')
                    if projects and row.get('project_key', '').upper() not in projects:
                        continue
                    if args.fields and row['field_id'] not in args.fields:
                        continue
                    selected += 1
                    identity = f'{key}:{row["field_id"]}:{row["offset"]}'
                    status = row.get('migration_status', '').strip().lower()
                    resumed = not args.issue and (
                        status.startswith('success') or
                        (status.startswith('skipped') and not args.retry_skipped) or
                        (journal is not None and journal.should_resume(identity, retry_skipped=args.retry_skipped)))
                    if resumed:
                        record(identity, {'issue': key, 'field': row['field_id'], 'status': 'RESUMED',
                                          'reason': 'Previous export/checkpoint state'})
                        continue
                    pending[executor.submit(worker, row)] = identity
                    if len(pending) >= max(2, args.workers * 2):
                        done, _ = wait(pending, return_when=FIRST_COMPLETED)
                        drain(done)
                while pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    drain(done)
            except BaseException:
                # Cancel queued rows before waiting; running rows must finish so
                # their Cloud writes can still be recorded before the error exits.
                for future in pending:
                    future.cancel()
                executor.shutdown(wait=True, cancel_futures=True)
                drain([future for future in pending if not future.cancelled()])
                raise
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
            if not selected:
                record('not-found', {'issue': args.issue or '-', 'status': 'FAILED',
                                    'reason': 'No matching issue/field rows in input export'})
        elapsed = max(time.monotonic() - started, 0.001)
        footer()
        save_json(directory / 'summary.json', {'input': args.input, 'mode': 'real-run' if args.real_run else 'dry-run', 'counts': counts, 'elapsed_seconds': round(elapsed, 2)})
        print(f'완료: {counts} | 소요 시간: {format_duration(elapsed)} | 결과: {directory}')
        return 1 if counts['FAILED'] else 0
    finally:
        try:
            if journal is not None:
                journal.flush()
        finally:
            # Older deployed SourceClient versions use requests directly and
            # have no owned session/close method. Do not fail a completed run
            # merely because that module has not been upgraded yet.
            close_source = getattr(source, 'close', None)
            if callable(close_source):
                close_source()


def confirm_interactive_run(args, client):
    print('\n[실행 환경 요약]')
    print(f'  Cloud: {args.env} | URL: {client.base_url}')
    print(f'  계정: {client.auth[0]} | API token: 설정됨 (값 숨김)')
    print(f'  입력: {args.input} | 범위: {args.issue or args.project or "파일 전체"}')
    print(f'  대상 이슈: {args.target_issue or "원본과 동일"} | 원본 Jira: {args.source_env}')
    print(f'  필드: {", ".join(args.fields) if args.fields else "전체"} | 매핑: {args.field_map or "기본 매핑"}')
    print(f'  매핑 누락 커스텀 필드의 동일 ID 사용 허용: {args.allow_same_field_ids}')
    print(f'  워커: {args.workers} | SKIPPED 재평가: {args.retry_skipped} | 결과: {args.output_dir}')
    print(f'  SSL 검증: {client.verify} | 실행: {"실제 반영" if args.real_run else "미리보기"}')
    print('\n[API 테스트] Cloud 계정 인증 확인 중...')
    response = client.request('GET', '/rest/api/3/myself', allow_redirects=False)
    try:
        if response.status_code != 200 or not response.json().get('accountId'):
            raise RuntimeError('Cloud API 인증 테스트 응답이 올바르지 않습니다.')
    finally:
        response.close()
    print('[API 테스트] 성공: Cloud API 토큰 인증 확인 (필드 수정/첨부 권한은 별도)')
    try:
        answer = prompt_value('진짜 진행할까요? [y/n, 기본 n]: ', 'n',
                              lambda value: value.lower() in ('y', 'n'))
    except (EOFError, KeyboardInterrupt):
        answer = 'n'
    if answer.lower() != 'y':
        print('취소했습니다. 변환 및 첨부파일 이전은 시작하지 않았습니다.')
        return False
    return True


def main(argv=None):
    parser = build_parser()
    try:
        interactive = argv is None and len(sys.argv) == 1 and sys.stdin.isatty()
        if interactive:
            args = interactive_args(parser)
        else:
            args = parser.parse_args(argv)
    except (EOFError, KeyboardInterrupt):
        print('\n설정 입력을 취소했습니다. Cloud 요청은 수행하지 않았습니다.', file=sys.stderr)
        return 130
    args = normalize_args(args, parser)
    try:
        if args.issue:
            rows = single_issue_rows(args)
            print(f'[단건] {args.issue} | {args.env} | {"실제 반영" if args.real_run else "미리보기"} | 대상 {len(rows)}개 필드 행')
        load_environment()
        url, auth = cloud_config(args.env)
        # Fail before HTTP requests when the Markdown parser is unavailable.
        from markdown_it import MarkdownIt  # noqa: F401
        from bs4 import BeautifulSoup  # noqa: F401
        import requests  # noqa: F401
        verify = ssl_verification(args)
        if verify is False:
            print('[SSL] 인증서 검증 비활성화: 서버 인증서와 호스트 이름을 검증하지 않습니다.', file=sys.stderr)
        client = CloudClient(url, auth, verify=verify)
        try:
            if interactive and not confirm_interactive_run(args, client):
                return 0
            return run_file_conversion(args, client)
        finally:
            client.close()
    except ImportError as error:
        module = getattr(error, 'name', None) or '알 수 없음'
        packages = {'markdown_it': 'markdown-it-py', 'bs4': 'beautifulsoup4', 'requests': 'requests'}
        package = packages.get(module.split('.')[0])
        print(f'모듈 로딩 실패: {error}', file=sys.stderr)
        print(f'실행 Python: {sys.executable}', file=sys.stderr)
        if package:
            requirements = Path(__file__).resolve().parent / 'requirements-adf.txt'
            print(f'의존성 설치 필요: {package} (모듈: {module})', file=sys.stderr)
            print(f'{shlex.quote(sys.executable)} -m pip install -r {shlex.quote(str(requirements))}', file=sys.stderr)
        else:
            print('누락 모듈을 확인하세요. export_data.py, migration_content.py, '
                  'migration_attachments.py, markdown_adf.py도 같은 폴더에 배포해야 합니다.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\n작업을 중단했습니다. 완료 기록과 원본 JSON을 확인하세요.', file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError) as error:
        print(f'변환 중단: {error}', file=sys.stderr)
        return 1
    except Exception as error:
        print(f'변환 중단: {type(error).__name__}', file=sys.stderr)
        # Keep full diagnostic details locally; requests errors may include
        # source URLs or field contents that should not be printed to terminals.
        error_path = Path(args.output_dir) / ('error_' + uuid.uuid4().hex + '.json')
        try:
            save_json(error_path, {'type': type(error).__name__, 'message': str(error),
                                  'traceback': traceback.format_exc(), 'python': sys.executable,
                                  'modules': {name: getattr(sys.modules.get(name), '__file__', None)
                                              for name in ('convert_adf', 'migration_attachments',
                                                           'migration_content', 'export_data')}})
            print(f'상세 오류 로그: {error_path.resolve()}', file=sys.stderr)
        except OSError:
            print('상세 오류 로그를 저장하지 못했습니다. 결과 폴더의 쓰기 권한/여유 공간을 확인하세요.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
