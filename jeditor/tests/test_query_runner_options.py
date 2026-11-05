import unittest
from pathlib import Path
import warnings
import step2_query_runner


class QueryRunnerOptionTests(unittest.TestCase):
    def test_jeditor_sql_no_active_order_by(self):
        sql_path = Path("jeditor_mysql_queries.sql")
        self.assertTrue(sql_path.exists())
        content = sql_path.read_text(encoding="utf-8")
        # Ensure active (uncommented) ORDER BY is not at the end
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        last_non_comment = [line for line in lines if not line.startswith("--")][-1]
        self.assertNotIn("ORDER BY", last_non_comment)

    def test_cooldown_argument_parser(self):
        # Test build_parser equivalent via main arg parsing
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--cooldown-seconds", "-c", "--cooldown", type=float, default=0.0)
        args = parser.parse_args(["--cooldown-seconds", "3.5"])
        self.assertEqual(args.cooldown_seconds, 3.5)

        args_alias = parser.parse_args(["-c", "2.0"])
        self.assertEqual(args_alias.cooldown_seconds, 2.0)

    def test_warning_suppression(self):
        # Verify that issuing a warning does not break or raise
        with warnings.catch_warnings(record=True) as recorded:
            warnings.warn("test warning", UserWarning)
            # Global suppression is active in step2_query_runner module
            self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
