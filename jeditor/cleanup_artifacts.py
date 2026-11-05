#!/usr/bin/env python3
"""
cleanup_artifacts.py
로그 파일(*.log) 및 레포트 HTML 파일(*report*.html 등)을 안전하게 정리하는 스크립트.
"""

import argparse
import sys
from pathlib import Path


def get_target_files(base_dir: Path, include_screenshots: bool = False, all_html: bool = False):
    """삭제 대상 파일 목록을 수집합니다."""
    targets = set()

    # 1. 로그 파일 (*.log)
    targets.update(base_dir.glob("*.log"))

    # 2. 레포트 HTML 파일
    if all_html:
        # readme.html 등을 포함한 모든 html (단, 필요시 주의)
        targets.update(base_dir.glob("*.html"))
    else:
        # 이름에 'report'가 들어간 html 파일만 타겟팅 (readme.html 등 보호)
        targets.update(base_dir.glob("*report*.html"))

    # 3. 스크린샷 디렉토리 내 파일 (옵션)
    if include_screenshots:
        screenshots_dir = base_dir / "screenshots"
        if screenshots_dir.exists() and screenshots_dir.is_dir():
            targets.update(p for p in screenshots_dir.iterdir() if p.is_file())

    return sorted(list(targets))


def format_size(size_bytes: int) -> str:
    """파일 크기를 사람이 읽기 쉬운 형식으로 변환합니다."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def main():
    parser = argparse.ArgumentParser(
        description="로그 파일 및 레포트 HTML 파일을 삭제하여 디렉토리를 정리합니다."
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="정리할 디렉토리 경로 (기본값: 스크립트가 위치한 폴더)",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="실제 삭제하지 않고 삭제 대상 파일 목록만 확인합니다.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="삭제 여부를 묻지 않고 즉시 삭제합니다.",
    )
    parser.add_argument(
        "--all-html",
        action="store_true",
        help="이름에 'report'가 없더라도 모든 *.html 파일을 삭제 대상에 포함합니다 (주의: readme.html 포함).",
    )
    parser.add_argument(
        "--include-screenshots",
        action="store_true",
        help="screenshots/ 디렉토리 내부의 생성 파일도 함께 삭제합니다.",
    )

    args = parser.parse_args()
    target_dir = args.dir.resolve()

    if not target_dir.exists() or not target_dir.is_dir():
        print(f"[오류] 유효한 디렉토리가 아닙니다: {target_dir}", file=sys.stderr)
        sys.exit(1)

    targets = get_target_files(
        base_dir=target_dir,
        include_screenshots=args.include_screenshots,
        all_html=args.all_html,
    )

    if not targets:
        print(f"[알림] 삭제할 대상 파일(로그/레포트)이 없습니다. (대상 폴더: {target_dir})")
        return

    print(f"=== 삭제 대상 파일 목록 ({target_dir}) ===")
    total_size = 0
    for file_path in targets:
        try:
            size = file_path.stat().st_size
            total_size += size
            rel_path = file_path.relative_to(target_dir)
            print(f" - {rel_path} ({format_size(size)})")
        except OSError:
            print(f" - {file_path}")

    print(f"총 {len(targets)}개 파일 / 약 {format_size(total_size)}")

    if args.dry_run:
        print("\n[Dry-run] 시뮬레이션 모드이므로 실제로 삭제하지 않았습니다.")
        return

    # 대화형 확인
    if not args.yes:
        confirm = input("\n위 파일들을 정말 삭제하시겠습니까? (y/N): ").strip().lower()
        if confirm not in ("y", "yes"):
            print("[취소] 삭제 작업을 취소했습니다.")
            return

    # 삭제 실행
    deleted_count = 0
    for file_path in targets:
        try:
            file_path.unlink()
            deleted_count += 1
            print(f"[삭제 완료] {file_path.name}")
        except Exception as e:
            print(f"[삭제 실패] {file_path.name}: {e}", file=sys.stderr)

    print(f"\n정리 완료: 총 {deleted_count}개 파일이 삭제되었습니다.")


if __name__ == "__main__":
    main()
