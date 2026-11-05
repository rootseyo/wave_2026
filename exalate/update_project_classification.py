#!/usr/bin/env python3
"""테스트/운영 환경에서 실행. auth.py는 저장소 루트에 별도로 배치한다."""
import argparse
import base64
from datetime import datetime, timezone
from html import escape
import importlib.util
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
TITLE = 'HMG-ADE Exalate 연동 현황 및 마이그레이션 대상 프로젝트 분류'
PAGE_ID = '24656478452'
GROUPS = {
    'REQ_IVI': 'CCIC27TRAC CCICTRACK CCNCBTRACK CCNCTRACK CCRCTRACK CSTRACK CONNLIGHT D2VTRACK DCUCTRACK DCTRACK POIPTRACK PBVTRACK P5TRACK P6TRACK S4TRACK S5LTRACK S5MTRACK S5WLTRACK S5WMTRACK'.split(),
    'CPMS': 'MLBMEXT ROA BICIM ROAS PDCCOWORK UIP PSMSCM BPCMWTC07 ECDPART ECDPARTL WAC ATCU PTGPSD ELECBODY ELECCONV ELECSW BDC CCU2S SPW RCMJIRA ISCMMOBIS ISCMAUMOVI ISCMVEONEE WPC'.split(),
    'CPMS_IVI': 'GENESISP HMCP KMCP'.split(),
    'FREQ': ['FREQ'],
    'NE1a': ['NE1AWJIMS'],
    'REQ_ELEC': 'ATCU TMF SPLIT MOBCR PDCX PTGPSD PDC'.split(),
}
EXCLUDED = 'ADEPRK DITPMSRX DK ECDPS ELECCCU ELECPMS ELECPOWER IBUMOBASE PLBM'.split()


def panel(kind, title, content):
    return (f'<ac:structured-macro ac:name="{kind}" ac:schema-version="1">'
            f'<ac:parameter ac:name="title">{escape(title)}</ac:parameter>'
            f'<ac:rich-text-body>{content}</ac:rich-text-body></ac:structured-macro>')


def table(headers, rows):
    head = ''.join(f'<th>{escape(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
    return f'<table data-layout="full-width"><tbody><tr>{head}</tr>{body}</tbody></table>'


def document(confirmed):
    summary = ('제공 목록 기준 마이그레이션 포함 프로젝트는 <strong>53개</strong>입니다.'
               if confirmed else '제공된 현황 요약의 포함 프로젝트 수는 <strong>33개</strong>이나, 상세 목록은 중복 제거 기준 <strong>53개</strong>로 집계되어 확정 전 정합성 확인이 필요합니다.')
    content = '<h2>1. 개요</h2><p>본 문서는 Atlassian Cloud 마이그레이션 대상 프로젝트 키와 기존 HMG Exalate Trigger 연동 프로젝트 키 간의 정합성을 분석하고, 이관 범위에 미포함된 연동 대상 프로젝트를 식별하기 위한 자료입니다.</p><p><strong>문서 대상:</strong> 운영유지보수 파트 고객 · <strong>작성 관점:</strong> 마이그레이션 담당자</p>'
    content += '<h2>2. 현황 요약</h2>' + panel('info', '연동 및 이관 범위 요약', f'<p>{summary}</p><ul><li>커넥션별 포함 등록 건수: <strong>55건</strong> / 고유 프로젝트 키: <strong>53개</strong></li><li>ATCU 및 PTGPSD는 CPMS SYNC와 REQ_ELEC SYNC에 각각 등록되어 있습니다.</li><li>트리거에 등록되어 있으나 이관 목록에 미포함된 프로젝트: <strong>9개</strong> (CPMS SYNC)</li></ul>')
    content += panel('warning', '사전 확인 필요', '<p>동기화 이슈를 방지하기 위해 미포함 프로젝트의 활성 연동 상태 및 이관 범위를 사전에 확인해야 합니다. 트리거 등록 사실만으로 실제 동기화 활성 상태를 판단할 수 없습니다.</p>')
    content += '<h2>3. 마이그레이션 포함 프로젝트 — 커넥션별 상세</h2><p>아래 목록은 제공된 포함 대상 데이터입니다. 건수는 커넥션별 등록 기준이며, 동일 키가 여러 커넥션에 포함될 수 있습니다.</p>'
    content += table(['커넥션', '등록 건수', '프로젝트 키'], [(f'[HMG-ADE] {escape(name)} SYNC', str(len(keys)), '<br />'.join(', '.join(f'<code>{k}</code>' for k in keys[i:i+4]) for i in range(0, len(keys), 4))) for name, keys in GROUPS.items()])
    content += '<h2>4. 마이그레이션 미포함 프로젝트 — 총 9개</h2><p>전체 대상은 <strong>[HMG-ADE] CPMS SYNC</strong> 트리거 등록 건입니다. 미포함은 제공된 이관 목록 기준이며, 의도적 제외 여부는 확인이 필요합니다.</p>'
    rows = []
    for key in EXCLUDED:
        note = ('이관 목록의 ADEPRKHKL과 명칭 유사성 확인 및 동일 프로젝트·오기재 여부 검토' if key == 'ADEPRK' else '실제 활성 연동 상태 및 동기화 중단 여부 검토' if key == 'DITPMSRX' else '실제 활성 연동 상태 및 Cloud 이관 대상 추가 여부 확인')
        rows.append((f'<code>{key}</code>', '미포함', escape(note)))
    content += table(['프로젝트 키', '이관 목록 분류', '확인 사항'], rows)
    content += '<h2>5. 향후 조치 권고사항</h2>'
    content += panel('info', '이관 범위 및 프로젝트 식별 확정', '<ol><li>운영유지보수 파트는 미포함 9개 프로젝트의 실제 활성 연동 상태를 확인하고, 마이그레이션 담당자와 Cloud 이관 대상 추가 여부를 결정합니다.</li><li>ADEPRK와 ADEPRKHKL의 프로젝트 식별 정보 및 트리거 조건을 대조하여 동일성·오기재 여부를 검토합니다. 명칭 유사성만으로 동일 프로젝트로 판단하지 않습니다.</li><li>포함 프로젝트 수와 최종 이관 목록의 정합성을 확정하고 결정 내용을 기록합니다.</li></ol>')
    content += panel('warning', 'Exalate 동기화 정지 및 전환 계획', '<p>Cloud 마이그레이션 전 Exalate 동기화 정지(Pause) 시점과 Cloud 전환·재개 시점을 합의합니다. 운영유지보수 파트와 마이그레이션 담당자는 대상 커넥션, 미처리 동기화 건, 전환 후 검증 기준 및 문제 발생 시 복구 절차를 사전에 정리해야 합니다.</p>')
    return content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-53', action='store_true', help='사용자 확인 후 제공 목록의 고유 프로젝트 수 53개를 확정')
    parser.add_argument('--apply', action='store_true', help='테스트/운영 환경에서 대상 페이지 제목과 본문 전체 교체')
    args = parser.parse_args()
    body = document(args.confirm_53)
    output = Path(__file__).with_name('project_classification.storage.html')
    output.write_text(body, encoding='utf-8')
    print(f'문서 생성: {output}')
    if not args.apply:
        return
    spec = importlib.util.spec_from_file_location('confluence_auth', ROOT / 'auth.py')
    auth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auth)
    if auth.DOMAIN != 'cloud-osci.atlassian.net':
        raise ValueError('auth.py DOMAIN이 대상 사이트와 다릅니다.')
    credential = base64.b64encode(f'{auth.EMAIL}:{auth.API_TOKEN}'.encode()).decode()
    headers = {'Authorization': f'Basic {credential}', 'Content-Type': 'application/json'}
    url = f'https://{auth.DOMAIN}/wiki/api/v2/pages/{PAGE_ID}'
    def request(method, endpoint, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    current = request('GET', url + '?body-format=storage')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path(__file__).with_name(f'page_{PAGE_ID}_{stamp}.backup.json')
    backup.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    result = request('PUT', url, {'id': PAGE_ID, 'status': 'current', 'title': TITLE,
        'body': {'representation': 'storage', 'value': body},
        'version': {'number': current['version']['number'] + 1, 'message': 'Exalate 연동 현황 및 마이그레이션 프로젝트 분류 정리'}})
    saved = request('GET', url + '?body-format=storage')
    if saved['title'] != TITLE or saved['version']['number'] != result['version']['number']:
        raise RuntimeError('저장 결과 확인 실패. 대상 페이지를 확인하세요.')
    saved_body = saved['body']['storage']['value']
    if any(key not in saved_body for keys in GROUPS.values() for key in keys) or any(key not in saved_body for key in EXCLUDED):
        raise RuntimeError('저장된 본문의 프로젝트 키 확인 실패.')
    print(f"반영 및 프로젝트 키 확인 완료: 페이지 {PAGE_ID}, 버전 {saved['version']['number']}; 기존 본문 백업: {backup}")


if __name__ == '__main__':
    main()
