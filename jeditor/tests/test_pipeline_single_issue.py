import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from export_helpers import source_row, write_export
from pipeline_single_issue import (
    main,
    parse_issue_key,
    parse_steps,
    run_step1,
    run_step2,
    run_step3,
)


class SingleIssuePipelineTests(unittest.TestCase):
    def test_parse_issue_key_valid(self):
        pkey, num = parse_issue_key("AAOSFRWDEV-543")
        self.assertEqual(pkey, "AAOSFRWDEV")
        self.assertEqual(num, 543)

        pkey, num = parse_issue_key("proj_1-100")
        self.assertEqual(pkey, "PROJ_1")
        self.assertEqual(num, 100)

    def test_parse_issue_key_invalid(self):
        with self.assertRaises(ValueError):
            parse_issue_key("INVALID")
        with self.assertRaises(ValueError):
            parse_issue_key("123-456")
        with self.assertRaises(ValueError):
            parse_issue_key("")

    def test_parse_steps(self):
        self.assertEqual(parse_steps("1,2,3,4"), {1, 2, 3, 4})
        self.assertEqual(parse_steps("1-4"), {1, 2, 3, 4})
        self.assertEqual(parse_steps("2,4"), {2, 4})
        self.assertEqual(parse_steps("3"), {3})
        with self.assertRaises(ValueError):
            parse_steps("abc")

    def test_step1_sql_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            # Default: all_fields=True (relaxed filter)
            sql_path = run_step1("TEST-123", "TEST", 123, out_dir, all_fields=True)
            self.assertTrue(sql_path.exists())
            content = sql_path.read_text(encoding="utf-8")
            self.assertIn("SELECT 'TEST' AS pkey", content)
            self.assertIn("AND i.issuenum = 123", content)
            self.assertIn("cfv.textvalue LIKE '%jeditor%'", content)
            self.assertIn("cfv.textvalue LIKE '%</%'", content)

            # Strict mode: all_fields=False
            sql_strict = run_step1("TEST-123", "TEST", 123, out_dir, all_fields=False)
            strict_content = sql_strict.read_text(encoding="utf-8")
            self.assertIn("AND i.issuenum = 123", strict_content)
            self.assertIn("cfv.textvalue LIKE '%jeditor%'", strict_content)

    def test_step2_fallback_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            source_file = write_export(
                out_dir / "data.html",
                [
                    source_row(issue_key="TEST-1", content="<p>content 1</p>"),
                    source_row(issue_key="OTHER-2", content="<p>content 2</p>"),
                ],
            )
            dummy_sql = out_dir / "query.sql"
            dummy_sql.write_text("SELECT 1;")

            with patch("step2_query_runner.get_db_config", return_value={"host": ""}):
                html_path, count = run_step2(
                    "TEST-1", "TEST", dummy_sql, out_dir, fallback_input=source_file
                )
            self.assertTrue(html_path.exists())
            self.assertEqual(count, 1)
            html_text = html_path.read_text(encoding="utf-8")
            self.assertIn("TEST-1", html_text)
            self.assertNotIn("OTHER-2", html_text)

    def test_step3_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            html_path = write_export(
                out_dir / "data.html",
                [source_row(issue_key="TEST-1", field_name="Description")],
            )
            stats = run_step3(html_path, out_dir)
            self.assertEqual(stats["total_rows"], 1)
            self.assertTrue((out_dir / "step3_analysis_report.txt").exists())
            self.assertTrue((out_dir / "step3_data_dashboard.html").exists())

    def test_full_pipeline_run(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            source_file = write_export(
                out_dir / "source.html",
                [source_row(issue_key="CFMT-3", content="<h2>Test Content</h2>\n<p>|A|B|</p>")],
            )

            # Mock Step 4 convert_adf.main to return 0
            with patch("convert_adf.main", return_value=0) as mock_adf_main:
                ret = main(
                    [
                        "CFMT-3",
                        "--input",
                        str(source_file),
                        "--output-dir",
                        str(out_dir),
                        "--dry-run",
                    ]
                )
                self.assertEqual(ret, 0)
                mock_adf_main.assert_called_once()
                self.assertTrue((out_dir / "step1_query_CFMT-3.sql").exists())
                self.assertTrue((out_dir / "step2_data_CFMT-3.html").exists())
                self.assertTrue((out_dir / "step3_analysis_report.txt").exists())
                self.assertTrue((out_dir / "step3_data_dashboard.html").exists())


if __name__ == "__main__":
    unittest.main()
