import unittest
from pathlib import Path
import tempfile
import html

from step5_check_jeditor_templates_sql import (
    parse_html_table_dump,
    generate_confluence_table_html,
    generate_confluence_table_markdown,
    write_template_report,
    write_confluence_report,
    clean_visibility,
    clean_template_content_html,
)

SAMPLE_DUMP_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"/></head><body><table>
<tr><th>PROJECT_KEY</th><th>PROJECT_NAME</th><th>TEMPLATE_ID</th><th>TEMPLATE_NAME</th><th>DESCRIPTION</th><th>FIELD_ID</th><th>FIELD_NAME</th><th>ISSUE_TYPES</th><th>AUTOFILL_ENABLED</th><th>IS_SHARED</th><th>VISIBILITY_FIELDS</th><th>VISIBILITY_TYPES</th><th>VISIBILITY_GROUPS</th><th>CONTENT</th></tr>
<tr><td>CFMT</td><td>커넥티드카 멀티미디어</td><td>101</td><td>결함 보고 템플릿</td><td>기본 결함 리포트 양식</td><td>description</td><td>Description</td><td>Bug, Defect</td><td>YES</td><td>1</td><td>EMPTY_VALUE</td><td>EMPTY_VALUE</td><td>EMPTY_VALUE</td><td><p><b>1. 결함 개요</b></p><p>- 재현 경로:</p></td></tr>
<tr><td>AAOSDEV</td><td>HMG AAOS Framework</td><td>201</td><td>Feature Spec</td><td>신규 기능 사양</td><td>customfield_10010</td><td>Specification</td><td>New Feature</td><td>NO</td><td>0</td><td>EMPTY_VALUE</td><td>EMPTY_VALUE</td><td>EMPTY_VALUE</td><td><p>Spec content</p></td></tr>
<tr><td>CFMT</td><td>커넥티드카 멀티미디어</td><td>103</td><td>필드 미지정 템플릿</td><td>테스트용</td><td>EMPTY_VALUE</td><td>EMPTY_VALUE</td><td>Bug</td><td>NO</td><td>0</td><td>EMPTY_VALUE</td><td>EMPTY_VALUE</td><td>EMPTY_VALUE</td><td><p>본문 내용</p></td></tr>
<tr><td>AAOSDEV</td><td>HMG AAOS Framework</td><td>202</td><td>이슈타입 미지정 템플릿</td><td>테스트용</td><td>description</td><td>Description</td><td>EMPTY_VALUE</td><td>NO</td><td>0</td><td>EMPTY_VALUE</td><td>EMPTY_VALUE</td><td>EMPTY_VALUE</td><td><p>본문 내용</p></td></tr>
</table></body></html>"""

class Step5ConfluenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.sample_html_file = self.temp_path / "sample_dump.html"
        self.sample_html_file.write_text(SAMPLE_DUMP_HTML, encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_html_table_dump(self):
        records = parse_html_table_dump(self.sample_html_file, env_name="ops")
        self.assertEqual(len(records), 4)
        
        r1 = records[0]
        self.assertEqual(r1["project"], "CFMT")
        self.assertEqual(r1["project_name"], "커넥티드카 멀티미디어")
        self.assertEqual(r1["template_id"], "101")
        self.assertEqual(r1["name"], "결함 보고 템플릿")
        self.assertEqual(r1["fields_str"], "Description")
        self.assertTrue(r1["is_migration_target"])
        self.assertTrue(r1["has_field"])
        self.assertTrue(r1["has_issue_type"])
        self.assertIn("cleaned_content", r1)
        self.assertIn("1. 결함 개요", r1["cleaned_content"])
        
        r2 = records[1]
        self.assertEqual(r2["project"], "AAOSDEV")
        self.assertEqual(r2["template_id"], "201")
        self.assertTrue(r2["is_migration_target"])
        self.assertIn("Spec content", r2["cleaned_content"])

        # Row 3: Missing field
        r3 = records[2]
        self.assertFalse(r3["has_field"])
        self.assertTrue(r3["has_issue_type"])
        self.assertFalse(r3["is_migration_target"])
        self.assertEqual(r3["migration_status"], "EXCLUDED_NO_FIELD")
        self.assertIn("미지정", r3["fields_str"])

        # Row 4: Missing issue type
        r4 = records[3]
        self.assertTrue(r4["has_field"])
        self.assertFalse(r4["has_issue_type"])
        self.assertFalse(r4["is_migration_target"])
        self.assertEqual(r4["migration_status"], "EXCLUDED_NO_ISSUETYPE")
        self.assertIn("미지정", r4["issue_types_str"])

    def test_clean_template_content_html(self):
        src = '<p><b>1. 제목</b></p><br><script>alert(1)</script>'
        cleaned = clean_template_content_html(src)
        self.assertIn("1. 제목", cleaned)
        self.assertNotIn("<script", cleaned)

    def test_generate_confluence_table_html(self):
        records = parse_html_table_dump(self.sample_html_file, env_name="ops")
        conf_html = generate_confluence_table_html(records)
        
        self.assertIn('<table class="confluenceTable"', conf_html)
        self.assertIn("커넥티드카 멀티미디어", conf_html)
        self.assertIn("결함 보고 템플릿", conf_html)
        self.assertIn("Bug, Defect", conf_html)
        
        # Verify added column 템플릿 내용 (rendered content, not raw ADF json)
        self.assertIn(">템플릿 내용<", conf_html)
        self.assertIn("1. 결함 개요", conf_html)
        self.assertIn("- 재현 경로:", conf_html)
        
        # Verify removed columns are absent from header
        self.assertNotIn(">환경<", conf_html)
        self.assertNotIn(">템플릿 ID<", conf_html)
        self.assertNotIn(">공개 범위<", conf_html)
        self.assertNotIn(">Auto-fill<", conf_html)
        
        # Confluence table must not have interactive button or SVG
        self.assertNotIn("<button", conf_html)
        self.assertNotIn("<svg", conf_html)
        self.assertNotIn("preview-row", conf_html)

    def test_generate_confluence_table_markdown(self):
        records = parse_html_table_dump(self.sample_html_file, env_name="ops")
        conf_md = generate_confluence_table_markdown(records)
        
        # Verify Markdown headers with removed columns omitted and 템플릿 내용 added
        self.assertIn("| # | 프로젝트 | 템플릿명 | 설명 | 적용 필드 | 적용 이슈 유형 | 템플릿 내용 | Jira 설정 |", conf_md)
        self.assertIn("CFMT (커넥티드카 멀티미디어)", conf_md)
        self.assertIn("결함 보고 템플릿", conf_md)
        self.assertIn("1. 결함 개요", conf_md)

    def test_write_template_report_and_confluence_file(self):
        records = parse_html_table_dump(self.sample_html_file, env_name="ops")
        report_file = self.temp_path / "test_report.html"
        confluence_file = self.temp_path / "test_confluence.html"
        
        write_template_report(records, report_path=report_file, confluence_path=confluence_file)
        
        self.assertTrue(report_file.exists())
        self.assertTrue(confluence_file.exists())
        
        dashboard_content = report_file.read_text(encoding="utf-8")
        # Check for Confluence copy features in dashboard
        self.assertIn("📋 Confluence 표 복사", dashboard_content)
        self.assertIn("btnCopyFilteredTable", dashboard_content)
        self.assertIn("projectFilter", dashboard_content)
        self.assertIn("targetFilter", dashboard_content)
        self.assertIn("마이그레이션 대상", dashboard_content)
        self.assertIn("필터 결과 표 전체 복사", dashboard_content)
        self.assertIn("copyConfluenceTable", dashboard_content)
        self.assertIn("confluenceModal", dashboard_content)
        self.assertIn("템플릿 내용", dashboard_content)
        self.assertIn("template-content-preview", dashboard_content)
        self.assertIn("no-confluence", dashboard_content)
        
        # Check removed columns in dashboard table headers
        self.assertNotIn(">환경<", dashboard_content)
        self.assertNotIn(">ID<", dashboard_content)
        self.assertNotIn(">공개 범위<", dashboard_content)
        self.assertNotIn(">Auto-fill<", dashboard_content)
        
        conf_content = confluence_file.read_text(encoding="utf-8")
        self.assertIn("JEditor 템플릿 현황 리포트 (Confluence 복사용)", conf_content)
        self.assertIn("btnCopyFiltered", conf_content)
        self.assertIn("targetFilter", conf_content)
        self.assertIn("projectFilter", conf_content)
        self.assertIn("confluenceTable", conf_content)
        self.assertIn("템플릿 내용", conf_content)
        self.assertIn("1. 결함 개요", conf_content)

    def test_resolve_issue_types_function(self):
        from step5_check_jeditor_templates_sql import resolve_issue_types, load_issue_type_mappings

        mapping = {"1": "Bug", "3": "Task", "10000": "Epic", "10001": "Story", "10100": "결함"}

        # 1. Single numeric ID
        s1, l1 = resolve_issue_types("1", mapping)
        self.assertEqual(s1, "1(Bug)")
        self.assertEqual(l1, ["1(Bug)"])

        # 2. Comma separated numeric IDs
        s2, l2 = resolve_issue_types("10000, 10001", mapping)
        self.assertEqual(s2, "10000(Epic), 10001(Story)")
        self.assertEqual(l2, ["10000(Epic)", "10001(Story)"])

        # 3. Korean issue type
        s3, l3 = resolve_issue_types("10100", mapping)
        self.assertEqual(s3, "10100(결함)")

        # 4. Pre-formatted ID(Name)
        s4, l4 = resolve_issue_types("10000 (Epic), 3(Task)", mapping)
        self.assertEqual(s4, "10000(Epic), 3(Task)")

        # 5. Unknown ID preserved
        s5, l5 = resolve_issue_types("99999", mapping)
        self.assertEqual(s5, "99999")

        # 6. Empty / null
        s6, l6 = resolve_issue_types("EMPTY_VALUE", mapping)
        self.assertEqual(s6, "")
        self.assertEqual(l6, [])

        # 7. Inline CLI mapping parsing
        cli_map = load_issue_type_mappings(custom_map_str="500=Security, 600=Release")
        self.assertEqual(cli_map["500"], "Security")
        self.assertEqual(cli_map["600"], "Release")
        s7, _ = resolve_issue_types("500, 600", cli_map)
        self.assertEqual(s7, "500(Security), 600(Release)")

    def test_numeric_issue_types_in_html_dump(self):
        numeric_html = """<!DOCTYPE html><html><body><table>
<tr><th>PROJECT_KEY</th><th>TEMPLATE_ID</th><th>TEMPLATE_NAME</th><th>FIELD_NAME</th><th>ISSUE_TYPES</th><th>CONTENT</th></tr>
<tr><td>AUTO</td><td>301</td><td>결함 양식</td><td>Description</td><td>1, 3</td><td><p>결함 내용</p></td></tr>
<tr><td>AUTO</td><td>302</td><td>사양 양식</td><td>Description</td><td>10000</td><td><p>사양 내용</p></td></tr>
</table></body></html>"""
        test_file = self.temp_path / "numeric_dump.html"
        test_file.write_text(numeric_html, encoding="utf-8")

        test_mapping = {"1": "Bug", "3": "Task", "10000": "Epic"}
        records = parse_html_table_dump(test_file, env_name="ops", issue_type_map=test_mapping)

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["issue_types_str"], "1(Bug), 3(Task)")
        self.assertEqual(records[0]["issue_types"], ["1(Bug)", "3(Task)"])
        self.assertEqual(records[1]["issue_types_str"], "10000(Epic)")

        # Verify Confluence table output contains the formatted issue types
        conf_html = generate_confluence_table_html(records)
        self.assertIn("1(Bug), 3(Task)", conf_html)
        self.assertIn("10000(Epic)", conf_html)

        # Verify Markdown output contains the formatted issue types
        conf_md = generate_confluence_table_markdown(records)
        self.assertIn("1(Bug), 3(Task)", conf_md)
        self.assertIn("10000(Epic)", conf_md)

    def test_resolve_field_display_function(self):
        from step5_check_jeditor_templates_sql import resolve_field_display, load_custom_field_mappings

        mapping = {"customfield_10010": "Release Notes", "customfield_10020": "사양서 내용", "10010": "Release Notes"}

        # 1. customfield_ID with Name provided
        s1, l1 = resolve_field_display("customfield_10010", "Release Notes")
        self.assertEqual(s1, "customfield_10010(Release Notes)")
        self.assertEqual(l1, ["customfield_10010(Release Notes)"])

        # 2. customfield_ID with lookup mapping
        s2, l2 = resolve_field_display("customfield_10020", "", mapping)
        self.assertEqual(s2, "customfield_10020(사양서 내용)")

        # 3. Numeric ID only -> converted to customfield_ID(Name)
        s3, l3 = resolve_field_display("10010", "Release Notes")
        self.assertEqual(s3, "customfield_10010(Release Notes)")

        # 4. Standard system fields (Description / Environment)
        s4, _ = resolve_field_display("description", "Description")
        self.assertEqual(s4, "Description")

        s5, _ = resolve_field_display("environment", "")
        self.assertEqual(s5, "Environment")

        # 5. Pre-formatted ID(Name)
        s6, _ = resolve_field_display("customfield_10010(Release Notes)", "")
        self.assertEqual(s6, "customfield_10010(Release Notes)")

        # 6. Empty / null field
        s7, l7 = resolve_field_display("EMPTY_VALUE", "EMPTY_VALUE")
        self.assertEqual(s7, "")
        self.assertEqual(l7, [])

        # 7. Inline CLI custom fields mapping parsing
        cli_map = load_custom_field_mappings(custom_map_str="customfield_10999=보안로그, 10888=감사필드")
        self.assertEqual(cli_map["customfield_10999"], "보안로그")
        self.assertEqual(cli_map["customfield_10888"], "감사필드")
        s8, _ = resolve_field_display("customfield_10999", "", cli_map)
        self.assertEqual(s8, "customfield_10999(보안로그)")

    def test_customfield_id_and_name_in_html_dump(self):
        cf_html = """<!DOCTYPE html><html><body><table>
<tr><th>PROJECT_KEY</th><th>TEMPLATE_ID</th><th>TEMPLATE_NAME</th><th>FIELD_ID</th><th>FIELD_NAME</th><th>ISSUE_TYPES</th><th>CONTENT</th></tr>
<tr><td>AUTO</td><td>401</td><td>릴리즈 노트 양식</td><td>customfield_10010</td><td>Release Notes</td><td>1(Bug)</td><td><p>릴리즈 내용</p></td></tr>
</table></body></html>"""
        test_file = self.temp_path / "cf_dump.html"
        test_file.write_text(cf_html, encoding="utf-8")

        records = parse_html_table_dump(test_file, env_name="ops")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["fields_str"], "customfield_10010(Release Notes)")
        self.assertEqual(records[0]["fields"], ["customfield_10010(Release Notes)"])

        # Check Confluence HTML contains the combined format
        conf_html = generate_confluence_table_html(records)
        self.assertIn("customfield_10010(Release Notes)", conf_html)

        # Check Markdown contains the combined format
        conf_md = generate_confluence_table_markdown(records)
        self.assertIn("customfield_10010(Release Notes)", conf_md)

if __name__ == "__main__":
    unittest.main()
