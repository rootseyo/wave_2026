#!/usr/bin/env python3
"""Collect complete DefaultValue and renderer metadata before Step 5."""
import argparse
from pathlib import Path
import re
import sys
from datetime import datetime
import time

from default_value_policy import collect_snapshot, save_json, write_default_html
from step5_check_jeditor_templates_sql import get_db_config

SCRIPT_DIR = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projects', '-p', type=Path, default=SCRIPT_DIR.parent / 'projects.txt')
    parser.add_argument('--filter-projects', help='쉼표 구분 프로젝트 목록, projects.txt보다 우선')
    parser.add_argument('--env', choices=('ops', 'stg'), default='ops')
    parser.add_argument('--output', '-o', type=Path, default=SCRIPT_DIR / 'default_values.html')
    parser.add_argument('--json-output', type=Path, default=SCRIPT_DIR / 'default_values.json')
    parser.add_argument('--sql', type=Path, default=SCRIPT_DIR / 'jeditor_default_values_all.sql')
    parser.add_argument('--batch-size', '-b', type=int, default=20, help='한 번에 조회할 프로젝트/레이아웃 수 (기본 20)')
    parser.add_argument('--cooldown', '-c', type=float, default=0.5, help='배치 사이 대기 초 (기본 0.5)')
    parser.add_argument('--query-timeout', type=int, default=120, help='SELECT 서버 실행 제한 초 (기본 120)')
    args = parser.parse_args()
    if args.batch_size < 1 or not 0 <= args.cooldown <= 60 or args.query_timeout < 1:
        parser.error('batch-size/timeout은 양수, cooldown은 0~60초여야 합니다.')
    started = time.monotonic()
    def log(message):
        print(f'[{datetime.now():%H:%M:%S}] {message}', flush=True)
    try:
        if args.output.resolve() == args.json_output.resolve():
            raise ValueError('HTML과 JSON 출력 경로는 달라야 합니다.')
        if args.filter_projects:
            projects = [p.strip().upper() for p in args.filter_projects.split(',') if p.strip()]
        else:
            projects = [line.split('#', 1)[0].strip().upper() for line in args.projects.read_text(encoding='utf-8-sig').splitlines()]
            projects = [p for p in projects if p]
        projects = sorted(set(projects))
        if not projects or any(not re.fullmatch(r'[A-Z0-9_-]+', p) for p in projects):
            raise ValueError('유효한 대상 프로젝트 목록이 필요합니다.')
        # Invalidate the previous success record before a new collection starts.
        save_json(args.json_output, dict(schema_version=1, complete=False, environment=args.env, checked_projects=[]))
        log(f'기본값/Renderer 조회 시작: {len(projects)}개 프로젝트 / 배치 {args.batch_size} / 대기 {args.cooldown:g}초 / SQL 제한 {args.query_timeout}초')
        snapshot = collect_snapshot(get_db_config(), projects, args.env, args.sql, batch_size=args.batch_size, cooldown=args.cooldown, query_timeout=args.query_timeout, progress=log)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        log(f'HTML 리포트 저장 시작: {args.output}')
        write_default_html(args.output, snapshot)
        log('HTML 저장 완료, JSON 저장 시작')
        save_json(args.json_output, snapshot)
        log(f'전체 완료 / {time.monotonic() - started:.1f}초 / HTML: {args.output} / JSON: {args.json_output}')
        print('다음: python step5_check_jeditor_templates_sql.py --env ' + args.env)
    except Exception as error:
        print(f'오류: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
