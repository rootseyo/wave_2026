import os
import sys
import unittest
import tempfile
from unittest.mock import patch, MagicMock

import step2_import_cloud_boxes as step2

class TestStep2ContinueOnFailure(unittest.TestCase):

    def test_write_html_report_with_failures(self):
        """Verify that failed boxes are rendered in a dedicated section with error details."""
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".html", delete=False) as f:
            temp_report_path = f.name

        original_report_file = step2.REPORT_FILE
        step2.REPORT_FILE = temp_report_path
        try:
            sample_results = [
                {
                    "box": {
                        "boxId": 101,
                        "boxName": "Success Box",
                        "boxType": "Program",
                        "jira_projects": ["PROJ1"],
                        "owner_name": "Alice"
                    },
                    "new_box_id": "CLOUD-101",
                    "migrated_owner": "user-account-1",
                    "status": "SUCCESS"
                },
                {
                    "box": {
                        "boxId": 102,
                        "boxName": "Failed Box Alpha",
                        "boxType": "Agile",
                        "jira_projects": ["PROJ2"],
                        "owner_name": "Bob"
                    },
                    "new_box_id": None,
                    "migrated_owner": "user-account-1",
                    "status": "CREATE FAILED",
                    "error_detail": "HTTP 400: Box name already exists in target"
                }
            ]

            step2.write_html_report(sample_results)

            self.assertTrue(os.path.exists(temp_report_path))
            with open(temp_report_path, "r", encoding="utf-8") as rf:
                content = rf.read()

            # Check failure section
            self.assertIn("⚠️ Box 생성 실패 내역", content)
            self.assertIn("Failed Box Alpha", content)
            self.assertIn("HTTP 400: Box name already exists in target", content)
            self.assertIn("DC ID: 102", content)
            self.assertIn("Creation Failed", content)

            # Check success item is also present
            self.assertIn("Success Box", content)
            self.assertIn("CLOUD-101", content)
        finally:
            step2.REPORT_FILE = original_report_file
            if os.path.exists(temp_report_path):
                os.remove(temp_report_path)

    def test_write_html_report_without_failures(self):
        """Verify report banner when all boxes succeed."""
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".html", delete=False) as f:
            temp_report_path = f.name

        original_report_file = step2.REPORT_FILE
        step2.REPORT_FILE = temp_report_path
        try:
            sample_results = [
                {
                    "box": {
                        "boxId": 201,
                        "boxName": "Box Good",
                        "boxType": "Program",
                        "jira_projects": ["PROJ_OK"],
                        "owner_name": "Carol"
                    },
                    "new_box_id": "CLOUD-201",
                    "migrated_owner": "user-account-2",
                    "status": "SUCCESS"
                }
            ]

            step2.write_html_report(sample_results)

            with open(temp_report_path, "r", encoding="utf-8") as rf:
                content = rf.read()

            self.assertIn("Box 생성 실패 건이 없습니다", content)
            self.assertNotIn("⚠️ Box 생성 실패 내역", content)
        finally:
            step2.REPORT_FILE = original_report_file
            if os.path.exists(temp_report_path):
                os.remove(temp_report_path)

    @patch("step2_import_cloud_boxes.session.post")
    def test_create_box_api_returns_error_on_failure(self, mock_post):
        """Verify create_box_api returns (None, error_msg) on failure."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error in BigPicture"
        mock_post.return_value = mock_response

        box = {"boxName": "Error Box", "boxType": "Program"}
        box_id, error_detail = step2.create_box_api(box, dry_run=False)

        self.assertIsNone(box_id)
        self.assertIsNotNone(error_detail)
        self.assertIn("500", error_detail)

if __name__ == "__main__":
    unittest.main()
