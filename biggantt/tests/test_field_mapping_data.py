import json
import tempfile
import unittest
from pathlib import Path

from field_mapping_data import JiraValidator, NeedsReview, load_id_mappings, resolve_fields


class MappingInputTests(unittest.TestCase):
    def fixture(self, rows, suffix=";"):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "id-mappings.html"
        path.write_text("<script>const rawData = " + json.dumps(rows) + suffix + "</script>")
        return path

    def row(self, source, target):
        return {"entityType": "jira/classic:customField", "serverId": source, "cloudId": target}

    def test_conflicting_source_rejected(self):
        with self.assertRaises(NeedsReview):
            load_id_mappings(self.fixture([self.row("1", "10"), self.row("1", "20")]))

    def test_many_to_one_allowed_but_start_end_collision_rejected(self):
        path = self.fixture([self.row("1", "10"), self.row("2", "10")])
        self.assertEqual(len(load_id_mappings(path)[0]), 2)
        with self.assertRaises(NeedsReview):
            resolve_fields(path, "1", "2")

    def test_no_name_fallback_for_missing_id(self):
        with self.assertRaises(NeedsReview):
            resolve_fields(self.fixture([self.row("1", "10")]), "1", "2")

    def test_javascript_expression_not_evaluated(self):
        with self.assertRaises(NeedsReview):
            load_id_mappings(self.fixture([self.row("1", "10")], ".map(evil);"))

    def test_explicit_prefixes_and_hash(self):
        value = resolve_fields(self.fixture([self.row("1", "10"), self.row("2", "20")]), "customfield_1", "2")
        self.assertEqual(value["start_field_id"], "customfield_10")
        self.assertEqual(len(value["id_mappings_sha256"]), 64)


class ContextTests(unittest.TestCase):
    def validator(self, context_rows, projects, types):
        client = JiraValidator("https://example.atlassian.net", "test@example.com", "test")
        self.addCleanup(client.session.close)
        def get(path, params=None):
            if "/project/" in path:
                return {"id": "100", "key": "TEST", "issueTypes": [{"id": "1"}, {"id": "2"}]}
            return [{"id": "customfield_" + i, "name": "Same name", "schema": {
                "type": "date", "custom": "com.atlassian.jira.plugin.system.customfieldtypes:datepicker"
            }} for i in ["10", "20"]]
        client.get = get
        client.pages = lambda path: projects if path.endswith("projectmapping") else types if path.endswith("issuetypemapping") else context_rows
        return client

    def test_partial_issue_type_coverage_rejected(self):
        client = self.validator([{"id": "9", "isGlobalContext": True}], [], [{"contextId": "9", "issueTypeId": "1"}])
        with self.assertRaises(NeedsReview):
            client.validate("TEST", "10", "20")

    def test_multiple_contexts_cover_one_project(self):
        client = self.validator([{"id": "8"}, {"id": "9"}],
            [{"contextId": "8", "projectId": "100"}, {"contextId": "9", "projectId": "100"}],
            [{"contextId": "8", "issueTypeId": "1"}, {"contextId": "9", "issueTypeId": "2"}])
        result = client.validate("TEST", "10", "20")
        self.assertEqual(result["fields"]["start"]["context_ids"], ["8", "9"])
        self.assertEqual(len(result["fields"]["start"]["same_name_ids"]), 2)

    def test_wrong_project_rejected(self):
        client = self.validator([{"id": "9", "isAnyIssueType": True}], [{"contextId": "9", "projectId": "200"}], [])
        with self.assertRaises(NeedsReview):
            client.validate("TEST", "10", "20")


if __name__ == "__main__":
    unittest.main()
