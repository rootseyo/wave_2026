import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from biggantt_field_mapping_batch import load_projects, run_project


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = {"project_key": "ABC", "status": "VALIDATED",
                         "site_url": "https://example.atlassian.net", "project_id": "100",
                         "start_field_id": "customfield_1", "end_field_id": "customfield_2"}
        (self.root / "one.json").write_text(json.dumps(self.manifest))

    def batch(self, entries):
        path = self.root / "batch.json"
        path.write_text(json.dumps({"schema_version": 1, "projects": entries}))
        return path

    def entry(self, box="BOX-1", manifest="one.json"):
        return {"project_key": "ABC", "box_id": box, "manifest": manifest}

    def test_multiple_boxes_dedup_to_one_project(self):
        projects = load_projects(self.batch([self.entry(), self.entry("BOX-2")]))
        self.assertEqual(list(projects), ["ABC"])
        self.assertNotIn("error", projects["ABC"])
        self.assertEqual(projects["ABC"]["chosen"]["box_id"], "BOX-1")

    def test_conflicting_duplicate_blocks_whole_project(self):
        changed = dict(self.manifest, end_field_id="customfield_3")
        (self.root / "two.json").write_text(json.dumps(changed))
        projects = load_projects(self.batch([self.entry(), self.entry("BOX-2", "two.json")]))
        self.assertIn("Conflicting", projects["ABC"]["error"])

    def test_invalid_duplicate_does_not_leave_valid_candidate_executable(self):
        projects = load_projects(self.batch([self.entry(), self.entry("BOX-2", "missing.json")]))
        self.assertIn("error", projects["ABC"])

    def test_unknown_filter_fails_instead_of_silent_noop(self):
        with self.assertRaises(ValueError):
            load_projects(self.batch([self.entry()]), {"UNKNOWN"})

    def test_input_change_invalidates_review_fingerprint(self):
        path = self.batch([self.entry()])
        first = load_projects(path)["ABC"]["fingerprint"]
        (self.root / "one.json").write_text(json.dumps(dict(self.manifest, end_field_id="customfield_3")))
        self.assertNotEqual(first, load_projects(path)["ABC"]["fingerprint"])

    def test_child_missing_report_is_not_success(self):
        item = load_projects(self.batch([self.entry()]))["ABC"]
        args = SimpleNamespace(action="migrate", app_url="https://example.atlassian.net/app",
                               env="ops", profile=self.root / "profile", login_timeout=10,
                               interactive_login=False)
        with patch("biggantt_field_mapping_batch.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            with self.assertRaises(ValueError):
                run_project(item, args, self.root / "results")


if __name__ == "__main__":
    unittest.main()
