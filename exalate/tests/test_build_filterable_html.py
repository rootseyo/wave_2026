"""Tests for build_filterable_exalate_html.py."""

from pathlib import Path
from build_filterable_exalate_html import transform


def test_transform_generates_filterable_html(tmp_path: Path):
    source_html = (
        "<!DOCTYPE html>\n"
        "<html><head><title>Test</title></head>\n"
        "<body>\n"
        "<table>\n"
        "  <tr><th>ID</th><th>NAME</th><th>PROCESSOR</th></tr>\n"
        "  <tr><td>1</td><td>ATC Conn</td><td>replica.project.key == 'ATC'</td></tr>\n"
        "  <tr><td>2</td><td>KHD Conn</td><td>issue.projectKey = 'KHD'</td></tr>\n"
        "</table>\n"
        "</body></html>"
    )
    src_file = tmp_path / "source.html"
    dst_file = tmp_path / "output-filterable.html"
    src_file.write_text(source_html, encoding="utf-8")

    transform(src_file, dst_file)
    assert dst_file.exists()

    content = dst_file.read_text(encoding="utf-8")
    assert 'id="exalate-migration-filter-style"' in content
    assert 'id="exalate-migration-filter"' in content
    assert 'id="exalate-migration-filter-script"' in content
    assert '<option value="target" selected>마이그레이션 대상만</option>' in content
    assert "ef-empty-row" in content
    assert "forceTargetMode" in content
