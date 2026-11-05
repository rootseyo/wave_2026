#!/usr/bin/env python3
"""Build offline Dynamic Forms imports from a Step 5 report (no Jira writes)."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
from html.parser import HTMLParser

FIELD_MAP_FILENAME = 'id-mappings-jira-classic-customField.html'


def default_mapping_path():
    # Prefer the file alongside the report; also support the existing parent layout.
    local = Path(FIELD_MAP_FILENAME)
    parent = Path('..') / FIELD_MAP_FILENAME
    return local if local.is_file() or not parent.is_file() else parent


class MappingTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th'):
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        elif tag == 'tr':
            if len(self.row) >= 3:
                self.rows.append(dict(zip(('entityType', 'serverId', 'cloudId'), self.row)))


def load_mapping(path):
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.json':
        data = json.loads(text)
        if isinstance(data, dict):
            data = [dict(entityType='jira/classic:customField', serverId=k, cloudId=v)
                    for k, v in data.items()]
    else:
        match = re.search(r'\b(?:const|let|var)\s+rawData\s*=\s*', text)
        if match:
            data, _ = json.JSONDecoder().raw_decode(text[match.end():])
        else:
            table = MappingTable()
            table.feed(text)
            data = table.rows
    mapping = {}
    for row in data:
        if row.get('entityType') != 'jira/classic:customField':
            continue
        source = str(row['serverId']).removeprefix('customfield_')
        target = str(row['cloudId']).removeprefix('customfield_')
        if not source.isdigit() or not target.isdigit():
            raise ValueError('매핑 ID는 숫자 또는 customfield_숫자여야 합니다.')
        mapping.setdefault('customfield_' + source, set()).add('customfield_' + target)
    if not mapping:
        raise ValueError('customField 매핑이 없습니다.')
    return mapping


def load_records(path):
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.json':
        records = json.loads(text)
    else:
        match = re.search(r'\bconst\s+TEMPLATES_DATA\s*=\s*', text)
        if not match:
            raise ValueError('Step 5 template_report.html 또는 Export JSON이 필요합니다.')
        records, _ = json.JSONDecoder().raw_decode(text[match.end():])
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError('템플릿 데이터는 객체 배열이어야 합니다.')
    return records


def field_id(value):
    value = value.strip()
    match = re.match(r'^(customfield_\d+)(?:\(|$)', value)
    if match:
        return match[1]
    if value.lower() in ('description', 'environment'):
        return value.lower()
    raise ValueError(f'필드 ID를 확인할 수 없습니다: {value!r}')


def validate_document(doc):
    if not isinstance(doc, dict) or doc.get('type') != 'doc' or doc.get('version') != 1 or not doc.get('content'):
        raise ValueError('본문 ADF가 없거나 비어 있습니다. Step 5에서 ADF를 생성하세요.')
    def visit(node):
        if not isinstance(node, dict) or not isinstance(node.get('type'), str):
            raise ValueError('ADF 노드 형식 오류')
        if node['type'] in ('media', 'mediaSingle', 'mediaGroup', 'mention'):
            raise ValueError('첨부/이미지/멘션은 Cloud 참조를 별도로 이전해야 합니다.')
        for child in node.get('content', []):
            visit(child)
    visit(doc)


def build(records, sample, mapping, projects):
    if sample is not None and (sample.get('exportFileVersion') != 1 or not isinstance(sample.get('screenFields'), dict)):
        raise ValueError('지원하는 export 샘플 형식이 아닙니다.')
    outputs, audit, excluded, errors = {}, [], [], []
    for record in records:
        project = str(record.get('project', ''))
        if projects and project not in projects:
            continue
        label = f"{project}/{record.get('template_id', '?')}/{record.get('name', '')}"
        validation = record.get('settings_validation') or {}
        if record.get('is_migration_target') is not True or validation.get('version') != 1 or validation.get('status') != 'PASS':
            excluded.append(dict(template=label, reason='검증 통과한 마이그레이션 대상 아님',
                                 validation=validation, migration_status=record.get('migration_status'),
                                 detail='최신 Step 5로 리포트를 다시 생성하세요.' if not validation else record.get('migration_status_label')))
            continue
        policy = record.get('migration_policy') or {}
        if policy.get('version') != 1 or policy.get('status') != 'PASS':
            excluded.append(dict(template=label, reason='기본값 우선/Renderer 검증 미통과',
                                 policy=policy, detail='기본값 조회 후 최신 Step 5로 리포트를 다시 생성하세요.'))
            continue
        if not record.get('fields'):
            excluded.append(dict(template=label, reason='대상 필드 없음'))
            continue
        try:
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', project):
                raise ValueError('프로젝트 키가 올바르지 않습니다.')
            sources = set()
            for raw_field in record['fields']:
                if re.fullmatch(r'comment(?:\s*\(.*\))?', raw_field.strip(), re.IGNORECASE):
                    excluded.append(dict(template=label, source_field=raw_field,
                                         reason='댓글 템플릿 변환 미구현: 별도 이전 필요'))
                    continue
                sources.add(field_id(raw_field))
            if not sources:
                continue
            doc = record.get('adf_doc')
            validate_document(doc)
            value_key = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
            for source in sorted(sources):
                if source.startswith('customfield_'):
                    candidates = mapping.get(source, set())
                    if len(candidates) != 1:
                        raise ValueError(f'{source}: 매핑 누락 또는 중복 {sorted(candidates)}')
                    target = next(iter(candidates))
                else:
                    target = source
                # JEditor rich-text values use textarea ADF. An optional target export
                # can supply verified Cloud metadata instead of inferred metadata.
                name = str(record.get('field_name') or source)
                metadata = dict(id=target, name=name, supported=True)
                if target.startswith('customfield_'):
                    metadata['schema'] = dict(type='string', custom='com.atlassian.jira.plugin.system.customfieldtypes:textarea', customId=int(target.removeprefix('customfield_')))
                else:
                    metadata['schema'] = dict(type='string', system=target)
                if sample is not None:
                    metadata = sample['screenFields'].get(target)
                    if not metadata or metadata.get('supported') is not True:
                        raise ValueError(f'{source} → {target}: 샘플에 지원 대상 필드가 없습니다.')
                    if target.startswith('customfield_') and not str(metadata.get('schema', {}).get('custom', '')).endswith(':textarea'):
                        raise ValueError(f'{target}: ADF를 받는 textarea 필드가 아닙니다.')
                path = f'{project}/{target}/{value_key}/import.json'
                if path not in outputs:
                    payload = dict(exportFileVersion=1, screenFields={target: copy.deepcopy(metadata)},
                                   currentVersion=40, sourceUrl='', fields=[])
                    if sample is not None:
                        payload['currentVersion'] = sample.get('currentVersion', 40)
                        payload['sourceUrl'] = sample.get('sourceUrl', '')
                    rule_id = hashlib.sha256(f'{project}/{target}/{value_key}'.encode()).hexdigest()[:10]
                    payload['fields'] = [dict(fieldId=target, rules=[dict(
                        id=rule_id, name=str(record.get('name') or label),
                        actions=[dict(type='SV', value=copy.deepcopy(doc))], conditionsGroups=[])])]
                    outputs[path] = payload
                audit.append(dict(project=project, template=label, source_field=source,
                                  target_field=target, target_name=metadata.get('name', ''),
                                  file=path, issue_types=record.get('issue_types', []),
                                  has_autofill=record.get('has_autofill'), visibility=record.get('visibility'),
                                  metadata_source='export' if sample is not None else 'inferred textarea'))
        except (ValueError, TypeError, KeyError) as error:
            errors.append(f'{label}: {error}')
    if errors:
        raise ValueError('\n'.join(errors))
    if not outputs and not excluded:
        raise ValueError('생성할 템플릿이 없습니다.')
    return outputs, dict(mapping=audit, excluded=excluded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?', type=Path, default=Path('template_report.html'), help='Step 5 HTML report or exported JSON')
    parser.add_argument('--sample', type=Path, help='선택: Cloud 필드 메타데이터 검증용 export')
    parser.add_argument('--mapping', type=Path, default=default_mapping_path(), help='Cloud 대상 환경의 ID mapping HTML/JSON (기본: 현재 또는 상위 폴더의 ' + FIELD_MAP_FILENAME + ')')
    parser.add_argument('--project', action='append', help='대상 프로젝트 (반복 지정 가능)')
    parser.add_argument('--output-dir', type=Path, default=Path('dynamic_forms_import'), help='출력 폴더 (동일 경로의 기존 파일은 교체)')
    args = parser.parse_args()
    try:
        outputs, audit = build(load_records(args.input),
                               json.loads(args.sample.read_text(encoding='utf-8-sig')) if args.sample else None,
                               load_mapping(args.mapping), set(args.project or []))
        # Complete validation before creating or replacing any files.
        if args.output_dir.is_dir():
            print(f'출력 폴더가 이미 있습니다. 동일 경로의 기존 파일을 교체합니다: {args.output_dir}')
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in outputs.items():
            destination = args.output_dir / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        audit.update(input=str(args.input), sample=str(args.sample) if args.sample else None, mapping_file=str(args.mapping), files=list(outputs))
        (args.output_dir / 'mapping-report.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'{len(outputs)}개 import 파일 생성: {args.output_dir}')
        if audit['excluded']:
            print(f"제외 {len(audit['excluded'])}건: mapping-report.json의 excluded 확인 (댓글 등 별도 이전 필요)")
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f'오류: {error}\n')


if __name__ == '__main__':
    main()
