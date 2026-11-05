"""Read-only Jira DC settings snapshot and Default Value-first migration policy."""
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
import threading
import xml.etree.ElementTree as ET

POLICY_VERSION = 1
SCRIPT_DIR = Path(__file__).resolve().parent


def source_identity(config):
    return sha256(json.dumps([str(config.get(k, '')) for k in ('host', 'port', 'database')]).encode()).hexdigest()


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    temporary.replace(path)


class ValueText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.structure = False

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ('img', 'table', 'iframe', 'object', 'input', 'hr'):
            self.structure = True


def default_state(row):
    """Do not interpret unknown XML representations as absence of a default."""
    if row.get('field_configuration_id') is None:
        return 'UNKNOWN'
    if row.get('default_value_id') is None:
        return 'EMPTY'
    value = row.get('default_value_xml')
    if value is None:
        return 'UNKNOWN'
    if not str(value).strip():
        return 'EMPTY'
    try:
        root = ET.fromstring(value)
    except (ET.ParseError, ValueError):
        return 'UNKNOWN'
    if root.tag == 'null' and not list(root) and not (root.text or '').strip():
        return 'EMPTY'
    if root.tag != 'string' or list(root):
        # Non-text types/custom providers need a type-specific interpretation.
        return 'UNKNOWN'
    text = root.text or ''
    parser = ValueText()
    parser.feed(text)
    return 'PRESENT' if parser.structure or ''.join(parser.parts).replace('\xa0', ' ').strip() else 'EMPTY'


def collect_snapshot(config, projects, environment, sql_path=None, *, batch_size=20,
                     cooldown=0.5, query_timeout=120, progress=None):
    import pymysql
    if not projects or batch_size < 1 or cooldown < 0 or query_timeout < 1:
        raise ValueError('프로젝트 및 배치/대기/시간제한 설정을 확인하세요.')
    emit = progress or (lambda message: None)
    sql = Path(sql_path or SCRIPT_DIR / 'jeditor_default_values_all.sql').read_text(encoding='utf-8')
    if '__PROJECT_PLACEHOLDERS__' not in sql:
        raise ValueError('SQL에 프로젝트 범위 마커가 없습니다.')
    start = time.monotonic()
    captured_start = datetime.now(timezone.utc).isoformat()
    emit('DB 연결 시작 (단일 연결, 순차 조회)')
    conn = pymysql.connect(**config, charset='utf8mb4', cursorclass=pymysql.cursors.SSDictCursor,
                           connect_timeout=20, read_timeout=query_timeout + 30, write_timeout=30)
    try:
        with conn.cursor() as cursor:
            def query(label, statement, params=()):
                query_start = time.monotonic()
                rows = []
                done = threading.Event()
                def heartbeat():
                    while not done.wait(10):
                        emit(f'{label}: 진행 중, 수신 {len(rows):,}행 / {time.monotonic() - query_start:.1f}초')
                thread = threading.Thread(target=heartbeat, daemon=True)
                emit(f'{label}: 시작')
                thread.start()
                try:
                    cursor.execute(statement, params)
                    while True:
                        chunk = cursor.fetchmany(500)
                        if not chunk:
                            break
                        rows.extend(chunk)
                except Exception:
                    emit(f'{label}: 실패 ({time.monotonic() - query_start:.1f}초)')
                    raise
                finally:
                    done.set()
                    thread.join()
                emit(f'{label}: 완료 {len(rows):,}행 / {time.monotonic() - query_start:.1f}초')
                return rows

            version = query('DB 버전 확인', 'SELECT VERSION() AS version')[0]['version']
            query('읽기 전용 세션 설정', 'SET SESSION TRANSACTION READ ONLY')
            # Server-side limit actually stops expensive SELECTs; socket timeout alone does not.
            if 'mariadb' in version.lower():
                query('쿼리 시간 제한 설정', 'SET SESSION max_statement_time=%s', (query_timeout,))
            else:
                query('쿼리 시간 제한 설정', 'SET SESSION max_execution_time=%s', (query_timeout * 1000,))
            conn.rollback()
            # Verify renderer metadata before the potentially longer context scan.
            layouts = query('기본 Field Configuration', "SELECT id FROM fieldlayout WHERE layout_type='default'")
            query('Renderer 컬럼 확인', 'SELECT fieldlayout, fieldidentifier, renderertype FROM fieldlayoutitem LIMIT 0')
            conn.rollback()
            defaults, schemes = [], []
            batch_count = (len(projects) + batch_size - 1) // batch_size
            for offset in range(0, len(projects), batch_size):
                batch = projects[offset:offset + batch_size]
                number = offset // batch_size + 1
                prefix = f'배치 {number}/{batch_count}'
                emit(f'{prefix}: 프로젝트 {", ".join(batch)}')
                placeholders = ','.join(['%s'] * len(batch))
                conn.begin()
                existing = query(prefix + ' 프로젝트 확인', f'SELECT id, pkey FROM project WHERE pkey IN ({placeholders})', batch)
                missing = set(batch) - {r['pkey'] for r in existing}
                if missing:
                    raise ValueError('DB에 없는 프로젝트: ' + ', '.join(sorted(missing)))
                batch_defaults = query(prefix + ' 기본값/컨텍스트', sql.replace('__PROJECT_PLACEHOLDERS__', placeholders), batch)
                batch_schemes = query(prefix + ' Renderer 스킴', f"""SELECT p.pkey AS project_key, na.sink_node_id AS scheme_id,
                           se.issuetype AS issue_type_id, se.fieldlayout AS layout_id
                    FROM project p
                    LEFT JOIN nodeassociation na ON na.source_node_id=p.id
                      AND na.source_node_entity='Project' AND na.sink_node_entity='FieldLayoutScheme'
                      AND na.association_type='ProjectScheme'
                    LEFT JOIN fieldlayoutschemeentity se ON se.scheme=na.sink_node_id
                    WHERE p.pkey IN ({placeholders})""", batch)
                conn.rollback()  # Release the read view before local processing/cooldown.
                emit(prefix + ' 기본값 XML 분석 시작')
                for row in batch_defaults:
                    row['value_state'] = default_state(row)
                defaults.extend(batch_defaults)
                schemes.extend(batch_schemes)
                emit(f'{prefix}: 완료, 프로젝트 {offset + len(batch)}/{len(projects)}, 기본값/컨텍스트 누적 {len(defaults):,}행')
                if offset + len(batch) < len(projects) and cooldown:
                    emit(f'DB 조회 휴식 {cooldown:g}초')
                    time.sleep(cooldown)
            layout_ids = sorted({str(r['layout_id']) for r in schemes if r['layout_id'] is not None} | {str(r['id']) for r in layouts})
            items = []
            for offset in range(0, len(layout_ids), batch_size):
                batch = layout_ids[offset:offset + batch_size]
                items.extend(query(f'Renderer 상세 {offset + 1}~{offset + len(batch)}/{len(layout_ids)}',
                    'SELECT fieldlayout AS layout_id, fieldidentifier AS field_id, renderertype AS renderer FROM fieldlayoutitem WHERE fieldlayout IN (' + ','.join(['%s'] * len(batch)) + ')', batch))
                conn.rollback()
                if offset + len(batch) < len(layout_ids) and cooldown:
                    time.sleep(cooldown)
            types = query('이슈 유형 목록', 'SELECT id FROM issuetype')
            conn.rollback()
        emit(f'DB 조회 완료: 프로젝트 {len(projects)}, 컨텍스트 {len(defaults):,}행, Renderer {len(items):,}행 / {time.monotonic() - start:.1f}초')
        return dict(schema_version=1, complete=True, environment=environment,
                    source_identity=source_identity(config), captured_at=datetime.now(timezone.utc).isoformat(),
                    capture_started_at=captured_start, consistency='per-batch read transactions',
                    checked_projects=sorted(projects), defaults=defaults, renderer_schemes=schemes,
                    default_layout_ids=[str(r['id']) for r in layouts], renderer_items=items,
                    issue_type_ids=[str(r['id']) for r in types])
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()



def load_snapshot(path, environment, config):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if data.get('schema_version') != 1 or data.get('complete') is not True:
        raise ValueError('기본값 조회가 완료된 JSON이 아닙니다. run_default_values.py를 다시 실행하세요.')
    if data.get('environment') != environment:
        raise ValueError('기본값 JSON과 Step 5의 환경이 다릅니다.')
    if config.get('host') and data.get('source_identity') != source_identity(config):
        raise ValueError('기본값 JSON과 Step 5의 원본 DB가 다릅니다.')
    for key in ('checked_projects', 'defaults', 'renderer_schemes', 'default_layout_ids', 'renderer_items', 'issue_type_ids'):
        if not isinstance(data.get(key), list):
            raise ValueError(f'기본값 JSON의 {key} 정보가 없습니다.')
    return data


def is_all_type(value):
    return value is None or str(value).strip().upper() in ('', '0', 'ALL')


class Policy:
    def __init__(self, snapshot, renderer_keys=('jeditor-renderer',)):
        self.data = snapshot
        self.renderer_keys = set(renderer_keys)
        self.defaults = {}
        self.schemes = {}
        self.items = {}
        for row in snapshot['defaults']:
            self.defaults.setdefault((row['project_key'], row['field_id']), []).append(row)
        for row in snapshot['renderer_schemes']:
            self.schemes.setdefault(row['project_key'], []).append(row)
        for row in snapshot['renderer_items']:
            self.items.setdefault((str(row['layout_id']), row['field_id']), []).append(row['renderer'])

    def default_for(self, project, field, issue_type):
        if not field.startswith('customfield_'):
            return 'EMPTY', []  # This snapshot covers Jira custom-field DefaultValue settings.
        rows = self.defaults.get((project, field), [])
        candidates = [r for r in rows if is_all_type(r.get('issue_type_id')) or str(r['issue_type_id']) == issue_type]
        if not candidates:
            return 'UNKNOWN', []  # Missing context is not proof of no default.
        # Explicit project/category contexts take precedence over the global context.
        rank = {'Project': 2, 'Category': 1, 'Global': 0}
        highest = max(rank.get(r.get('context_scope'), -1) for r in candidates)
        candidates = [r for r in candidates if rank.get(r.get('context_scope'), -1) == highest]
        explicit = [r for r in candidates if not is_all_type(r.get('issue_type_id'))]
        if explicit:
            candidates = explicit
        ids = {(str(r.get('context_id')), str(r.get('field_configuration_id')), str(r.get('default_value_id'))) for r in candidates}
        if len(ids) != 1:
            return 'UNKNOWN', candidates
        return default_state(candidates[0]), candidates

    def renderer_for(self, project, field, issue_type):
        rows = self.schemes.get(project, [])
        schemes = {str(r['scheme_id']) for r in rows if r.get('scheme_id') is not None}
        if len(schemes) > 1 or not rows:
            return None
        if not schemes:
            layouts = self.data['default_layout_ids']
        else:
            matches = [r for r in rows if str(r.get('issue_type_id')) == issue_type]
            if not matches:
                matches = [r for r in rows if is_all_type(r.get('issue_type_id'))]
            layouts = {str(r['layout_id']) for r in matches if r.get('layout_id') is not None}
        if len(layouts) != 1:
            return None
        values = self.items.get((str(next(iter(layouts))), field), [])
        return values[0] if len(values) == 1 and values[0] else None

    def evaluate(self, record):
        result = dict(version=POLICY_VERSION, status='UNKNOWN', reasons=[], checks=[],
                      snapshot_at=self.data['captured_at'], environment=self.data['environment'])
        project = record['project']
        if project not in self.data['checked_projects']:
            result['reasons'] = ['기본값 조회 완료 프로젝트에 포함되지 않음']
            return result
        normalized = record.get('settings_validation', {}).get('normalized', {})
        fields = normalized.get('autofill_fields', [])
        types = normalized.get('autofill_types', [])
        if 'ALL' in types:
            # Conservative superset: every DC issue type, including unused types.
            types = self.data['issue_type_ids']
            result['all_types_basis'] = '전체 DC 이슈 유형 (보수적 검증)'
        if not fields or not types:
            result['reasons'] = ['검증할 필드·이슈 유형 범위 없음']
            return result
        for field in fields:
            for issue_type in types:
                state, defaults = self.default_for(project, field, issue_type)
                renderer = self.renderer_for(project, field, issue_type)
                result['checks'].append(dict(field_id=field, issue_type_id=issue_type,
                    default_state=state, default_values=[{k: r.get(k) for k in ('context_id', 'context_name', 'field_configuration_id', 'default_value_id', 'default_value_xml')} for r in defaults],
                    renderer=renderer, renderer_is_jeditor=renderer in self.renderer_keys))
        checks = result['checks']
        present = [c for c in checks if c['default_state'] == 'PRESENT']
        if present:
            full = len(present) == len(checks)
            result.update(status='DEFAULT_VALUE_PRIORITY' if full else 'PARTIAL_DEFAULT_VALUE',
                          reasons=['기본값 우선: 전체 범위 제외' if full else '기본값과 일부 범위 겹침: 템플릿 전체 검토 제외'])
        elif any(c['default_state'] == 'UNKNOWN' for c in checks):
            result['reasons'] = ['기본값 또는 컨텍스트를 확정할 수 없음']
        elif any(c['renderer'] is None for c in checks):
            result['reasons'] = ['적용 Field Configuration / Renderer 확인 불가']
        elif any(not c['renderer_is_jeditor'] for c in checks):
            result.update(status='NOT_JEDITOR_RENDERER', reasons=['일부 또는 전체 범위에 JEditor Renderer가 적용되지 않음'])
        else:
            result.update(status='PASS', reasons=['기본값 없음, 모든 대상 범위의 JEditor Renderer 확인'])
        return result

    def apply(self, records):
        for record in records:
            result = self.evaluate(record)
            record['migration_policy'] = result
            if result['status'] != 'PASS':
                record['is_migration_target'] = False
                record['migration_status'] = 'EXCLUDED_' + result['status']
                record['migration_status_label'] = '제외: ' + '; '.join(result['reasons'])
            record['verification_msg'] += ' / 기본값 우선 검증: ' + '; '.join(result['reasons'])


def write_default_html(path, snapshot):
    rows = [r for r in snapshot['defaults'] if r['value_state'] != 'EMPTY']
    columns = ['project_key', 'project_name', 'field_id', 'field_name', 'field_type', 'context_id',
               'context_name', 'context_scope', 'issue_type_id', 'field_configuration_id',
               'field_configuration_name', 'default_value_id', 'value_state', 'default_value_xml']
    with Path(path).open('w', encoding='utf-8') as out:
        out.write('<!doctype html><html lang="ko"><meta charset="utf-8"><title>기본값 설정 리포트</title><style>body{font:14px sans-serif;margin:24px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px;vertical-align:top}td{white-space:pre-wrap;max-width:500px;overflow-wrap:anywhere}</style><h1>기본값 설정 리포트</h1>')
        out.write('<p>HTML/일반 텍스트 기본값 및 해석 검토 대상. 조회 완료 프로젝트: ' + escape(', '.join(snapshot['checked_projects'])) + '</p><table><tr>')
        out.write(''.join('<th>' + c + '</th>' for c in columns) + '</tr>')
        for row in rows:
            out.write('<tr>' + ''.join('<td>' + escape(str(row.get(c) if row.get(c) is not None else '')) + '</td>' for c in columns) + '</tr>')
        out.write('</table></html>')
